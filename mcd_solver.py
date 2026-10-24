#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mcd_solver.py — 「麦门顶饿算账」核心引擎

把两套天生不通的数据缝起来算账：
  · 营养库（单品营养，无价格）
  · 门店菜单（真实售价，无营养）
算出三种人设的答案：吃最饱不胖 / 每块钱蛋白最多 / 过嘴瘾还不亏。

用法：
  python mcd_solver.py --city 周口市 --keyword 万达广场 --budget 35 --kcal 800
  python mcd_solver.py --city 周口市 --budget 35          # 不给门店名，自动列出该城市门店让人选
  python mcd_solver.py --city 周口市 --budget 35 --oneline # 只输出一句话，方便发微信
  python mcd_solver.py --store 3560111 --budget 30 --kcal 600 --json
"""
import argparse
import itertools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcd_client as mc
import catalog as ct

INDULGE_KEYS = ("堡", "翅", "鸡块", "鸡排", "鸡腿", "派", "冰淇淋", "麦旋风", "新地")
SODIUM_DAILY = 2000.0     # 中国居民每日钠建议上限 mg
JOG_KCAL_PER_MIN = 10.0   # 60kg 慢跑约 10 kcal/分钟
MIN_CAL_PROTEIN = 15.0    # 解馋档的蛋白下限，防止选出纯糖油
MIN_ECON_PROTEIN = 10.0   # 省钱档的蛋白下限，防止把薯饼当蛋白来源

TOKEN_HELP = """
没有找到麦当劳 MCP Token。先花一分钟领一个，然后任选一种方式配置。

一、去哪儿拿
    手机或电脑打开  https://open.mcd.cn/mcp
    点右上角「登录」，用手机号加验证码登录
    登录后点右上角「控制台」，点「激活」，同意协议
    Token 立刻生成，复制那串字符

二、怎么设置（三选一，任选一种）
    Windows 临时生效，只对当前窗口有效：
        set MCD_MCP_TOKEN=你的Token

    Windows 永久生效，设置完要关掉命令行窗口重新打开：
        setx MCD_MCP_TOKEN 你的Token

    macOS 或 Linux：
        export MCD_MCP_TOKEN=你的Token

三、或者改用配置文件
    在用户目录下的 .workbuddy/mcp.json 里加一个 mcd-mcp 连接器，
    Authorization 填  Bearer 你的Token
    仓库里的 mcp-config.example.json 就是现成模板，可以照着改

配置完再运行一次就行。Token 只能自己留着，不要写进仓库，也不要发给别人。
"""


def choose_store(stores, keyword):
    """
    query-nearby-stores 是按距离返回的，不会按关键词过滤。
    「七一路」这类关键词往往会先返回离得最近的另一家店，所以这里补一层关键词匹配。
    """
    tokens = [t for t in re.split(r"[\s,，、]+", keyword or "") if t]
    cands = [s for s in stores if s.get("businessStatus")] or list(stores)

    def score(s):
        hay = f"{s.get('storeName', '')}{s.get('address', '')}"
        return sum(1 for t in tokens if t in hay)

    return max(cands, key=score) if tokens else cands[0]


def sort_by_distance(stores):
    """按接口返回的 distance 升序。distance 单位是米。"""
    def d(s):
        try:
            return float(s.get("distance") or 0)
        except (TypeError, ValueError):
            return 0.0
    return sorted(stores, key=d)


def fmt_distance(meters):
    try:
        m = float(meters)
    except (TypeError, ValueError):
        return "距离未知"
    if m <= 0:
        return "距离未知"
    if m < 1000:
        return f"{m:.0f} 米"
    return f"{m / 1000:.1f} 公里"


def render_store_list(stores):
    """把候选门店排成给人看的列表。"""
    L = [f"按城市搜到 {len(stores)} 家门店（距离按城市中心估算，仅供排序参考）："]
    for i, s in enumerate(stores, 1):
        status = "营业中" if s.get("businessStatus") else "休息中"
        addr = (s.get("address") or "").strip()
        L.append(f"  {i}. {s.get('storeName', '')}　{fmt_distance(s.get('distance'))}　{status}"
                 + (f"　{addr}" if addr else ""))
    return "\n".join(L)


def prompt_pick_store(stores):
    """
    让人选一家。有交互终端就问，没有就直接取最近的一家。
    返回 (store, 是否自动选择)。
    """
    if len(stores) == 1:
        return stores[0], True
    interactive = False
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return stores[0], True
    print(f"选择第几家（直接回车 = 1，最近的一家）：", end="", file=sys.stderr, flush=True)
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return stores[0], True
    if not raw:
        return stores[0], False
    try:
        idx = int(raw)
        if 1 <= idx <= len(stores):
            return stores[idx - 1], False
    except ValueError:
        pass
    print("输入的不是有效序号，默认用最近的一家。", file=sys.stderr)
    return stores[0], True


def cn_money(x):
    """把 3.5 说成「3 块 5」，把 3.0 说成「3 块」。"""
    x = round(float(x), 2)
    if x <= 0:
        return "0 块"
    yuan = int(x)
    jiao = int(round((x - yuan) * 10))
    if jiao >= 10:
        yuan += 1
        jiao = 0
    if jiao == 0:
        return f"{yuan} 块"
    if yuan == 0:
        return f"{jiao} 毛"
    return f"{yuan} 块 {jiao}"


def build_pool(meals, nutrition):
    """只保留「菜单名 == 营养名」的单品。宁缺毋滥，绝不估猜价格。"""
    pool = []
    for code, m in meals.items():
        n = nutrition.get(m["name"])
        if not n or m["price"] <= 0:
            continue
        pool.append({"code": code, "name": m["name"], "price": m["price"],
                     "kcal": n["kcal"], "protein": n["protein"], "fat": n["fat"],
                     "carb": n["carb"], "sodium": n["sodium"], "calcium": n["calcium"]})
    return pool


def metrics(x):
    """展示用四舍五入，排序用原始值。

    踩过的坑：1.1739 和 1.1667 都四舍五入成 1.17，排序时被当成并列，
    结果按下一个键（蛋白数）选错了答案。所以排序一律用 *_raw。
    """
    price, kcal, protein = x["price"], x["kcal"], x["protein"]
    return {
        "g_per_yuan_raw": (protein / price) if price > 0 else 0.0,
        "g_per_yuan": round(protein / price, 2) if price > 0 else 0.0,
        "protein_density_raw": (protein / (kcal / 100.0)) if kcal > 0 else 0.0,
        "protein_density": round(protein / (kcal / 100.0), 1) if kcal > 0 else 0.0,
    }


def enumerate_combos(pool, budget, kcal_cap, max_items=3, max_qty=2):
    combos = []
    n = len(pool)
    # 套餐最多点 1 份：一个人点两份「三件套」等于两杯可乐两份薯条，现实里不会这么点
    qty_cap = [1 if pool[i].get("kind") == "套餐" else max_qty for i in range(n)]
    for k in range(1, max_items + 1):
        for idxs in itertools.combinations(range(n), k):
            for qtys in itertools.product(*[range(1, qty_cap[i] + 1) for i in idxs]):
                price = sum(pool[i]["price"] * q for i, q in zip(idxs, qtys))
                if price > budget + 1e-9:
                    continue
                kcal = sum(pool[i]["kcal"] * q for i, q in zip(idxs, qtys))
                if kcal_cap and kcal > kcal_cap + 1e-9:
                    continue
                protein = sum(pool[i]["protein"] * q for i, q in zip(idxs, qtys))
                if protein <= 0:
                    continue
                combos.append({
                    "items": [{"code": pool[i]["code"], "name": pool[i]["name"],
                               "kind": pool[i].get("kind", "单品"),
                               "price": pool[i]["price"], "qty": q} for i, q in zip(idxs, qtys)],
                    "price": round(price, 1),
                    "kcal": round(kcal),
                    "protein": round(protein, 1),
                    "fat": round(sum(pool[i]["fat"] * q for i, q in zip(idxs, qtys)), 1),
                    "carb": round(sum(pool[i]["carb"] * q for i, q in zip(idxs, qtys)), 1),
                    "sodium": round(sum(pool[i]["sodium"] * q for i, q in zip(idxs, qtys))),
                    # 组合里最"脏"那一件的密度，用来挡住拿冰淇淋/薯条凑蛋白的组合
                    "min_item_density": round(min(
                        (pool[i]["protein"] / (pool[i]["kcal"] / 100.0)) if pool[i]["kcal"] > 0 else 0.0
                        for i in idxs), 2),
                })
    for c in combos:
        c.update(metrics(c))
    return combos


def combo_key(c):
    return tuple(sorted((it["code"], it["qty"]) for it in c["items"]))


def _pick(cands, rank_key, used):
    """按 rank_key 从高到低取第一个没被别的档用过的组合。"""
    for c in sorted(cands, key=rank_key, reverse=True):
        k = combo_key(c)
        if k not in used:
            used.add(k)
            return c
    return None


def pick_tiers(combos):
    """三种人设，三个不同目标函数。刻意不做花哨加权分，全部可解释。"""
    tiers, used = {}, set()

    # ① 吃最饱不胖：热量受限下蛋白最多（同蛋白取热量更低者）
    #    组合里每一件都得够干净（密度 ≥ 3），否则会推荐「鳕鱼堡 + 冰淇淋」这种为 2 克蛋白塞甜品的结果
    cand = [c for c in combos
            if c["protein_density_raw"] >= 3.0 and c.get("min_item_density", 0) >= 3.0]
    if cand:
        c = _pick(cand, lambda c: (c["protein"], -c["kcal"]), used)
        if c:
            tiers["吃最饱不胖"] = c

    # ② 每块钱蛋白最多：不看热量只看钱，但设蛋白下限，别把薯饼当蛋白来源
    cand2 = [c for c in combos if c["protein"] >= MIN_ECON_PROTEIN]
    if cand2:
        c = _pick(cand2, lambda c: (c["g_per_yuan_raw"], c["protein"], -c["price"]), used)
        if c:
            tiers["每块钱蛋白最多"] = c

    # ③ 过嘴瘾还不亏：只选「一样」最能解馋的东西，不拼凑
    #    之前允许组合，会推出「套餐 + 苹果片」这种为了凑热量硬加一个配菜的答案，很怪
    cand3 = [c for c in combos
             if len(c["items"]) == 1
             and c["protein"] >= MIN_CAL_PROTEIN
             and any(any(k in it["name"] for k in INDULGE_KEYS) for it in c["items"])]
    if cand3:
        c = _pick(cand3, lambda c: (c["kcal"], c["protein"]), used)
        if c:
            tiers["过嘴瘾还不亏"] = c

    return tiers


def build_insight(combos, lean, pool):
    """
    两条洞察：
      ① 单品对撞：同样吃 300 大卡，效率之王 vs 效率之底，蛋白差几倍（永远成立）
      ② 同蛋白热量差：吃到差不多的蛋白，最费热量的吃法要多跑多少分钟（差异够大才说）
    """
    out = {}

    # 只跟「有热量的固体食物」比，别拿红茶/纯净水来垫底，那不叫对比
    ranked = sorted([p for p in pool if p["kcal"] >= 100 and p["protein"] > 0],
                    key=lambda x: metrics(x)["protein_density"], reverse=True)
    if len(ranked) >= 2:
        top, bottom = ranked[0], ranked[-1]
        base = 300.0
        out["duel"] = {
            "top": top, "bottom": bottom, "base_kcal": base,
            "top_protein": round(top["protein"] * base / top["kcal"], 1),
            "bottom_protein": round(bottom["protein"] * base / bottom["kcal"], 1),
            "times": round((top["protein"] / top["kcal"]) / (bottom["protein"] / bottom["kcal"]), 1)
            if bottom["protein"] > 0 else None,
        }

    if lean:
        p = lean["protein"]
        peers = [c for c in combos if p * 0.9 <= c["protein"] <= p * 1.1
                 and c["kcal"] > lean["kcal"]]
        if peers:
            worst = max(peers, key=lambda c: c["kcal"])
            delta = worst["kcal"] - lean["kcal"]
            if delta >= 100:   # 差距太小就不硬凑，免得像凑数
                out["gap"] = {"lean": lean, "worst": worst, "delta_kcal": delta,
                              "jog_min": round(delta / JOG_KCAL_PER_MIN)}

    return out


def budget_hint(pool, budget):
    """预算太低时，直接告诉他最低多少钱才能吃到像样的一餐。"""
    viable = [it for it in pool if it["protein"] >= MIN_ECON_PROTEIN]
    if not viable:
        return None
    cheapest = min(viable, key=lambda it: it["price"])
    if budget >= cheapest["price"]:
        return None
    return (f"预算 ¥{budget:g} 在这家店买不到够蛋白的东西。"
            f"最低 **¥{cheapest['price']:g}** 起——**{cheapest['name']}**"
            f"（{cheapest['protein']:.0f} g 蛋白 / {cheapest['kcal']:.0f} kcal）。")


def fmt_items(combo, mark=True):
    """数量是 1 就不显示 ×1，读起来干净。mark=False 用于发微信的句子，不带标记。"""
    parts = []
    for it in combo["items"]:
        tag = "［套餐］" if (mark and it.get("kind") == "套餐") else ""
        parts.append(tag + it["name"] + (f"×{it['qty']}" if it["qty"] > 1 else ""))
    return " + ".join(parts)


def render_oneline(store, budget, kcal_cap, tiers, insight):
    """
    只输出一句话，方便直接发微信。
    形如：35 块 800 大卡，点薄皮焦香V翅×2，384 大卡 34 克蛋白，比培根安格斯厚牛堡省 3 块 5、少 323 大卡。
    """
    best = tiers.get("吃最饱不胖")
    if not best:
        return f"{budget:g} 块 {kcal_cap:.0f} 大卡，这家门店凑不出像样的一餐，把预算或热量放宽一点。"

    head = f"{budget:g} 块"
    if kcal_cap:
        head += f" {kcal_cap:.0f} 大卡"
    line = (f"{head}，点{fmt_items(best, mark=False)}，"
            f"{best['kcal']:.0f} 大卡 {best['protein']:g} 克蛋白")

    gap = (insight or {}).get("gap")
    if gap:
        other = gap["worst"]
        diff_money = best["price"] - other["price"]
        seg = []
        if abs(diff_money) >= 0.05:
            seg.append(f"{'省' if diff_money < 0 else '多花'} {cn_money(abs(diff_money))}")
        if gap["delta_kcal"] > 0:
            seg.append(f"少 {gap['delta_kcal']:.0f} 大卡")
        if seg:
            line += f"，比{fmt_items(other, mark=False)}{'、'.join(seg)}"

    return line + "。"


def render(store, budget, kcal_cap, pool, tiers, top_items, verified, insight, hint=None):
    L = [f"# 麦门顶饿算账｜{store.get('storeName', '')}", ""]
    L.append(f"预算 ≤ **¥{budget:g}**" + (f"｜热量 ≤ **{kcal_cap:.0f} kcal**" if kcal_cap else ""))
    L.append("")
    if hint:
        L.append(f"> **提醒：** {hint}")
        L.append("")

    order = [("吃最饱不胖", "热量预算内，蛋白最多的那一单"),
             ("每块钱蛋白最多", "不看热量，只问每块钱能换多少蛋白"),
             ("过嘴瘾还不亏", "要炸的，但也别光吃油")]
    L.append("## 三档答案")
    L.append("")
    for key, sub in order:
        c = tiers.get(key)
        if not c:
            continue
        L.append(f"### {key}")
        L.append(f"*{sub}*")
        L.append("")
        L.append(f"**{fmt_items(c)}**")
        L.append("")
        L.append(f"- 实付 **¥{c['price']:g}**｜热量 **{c['kcal']:.0f} kcal**｜蛋白 **{c['protein']:.1f} g**")
        L.append(f"- 每块钱换蛋白 **{c['g_per_yuan']} g**｜蛋白密度 **{c['protein_density']} g / 100 kcal**")
        na = c["sodium"]
        pct = na / SODIUM_DAILY * 100
        warn = "（一餐就吃掉大半日额度，当天别再重口）" if pct >= 50 else ""
        L.append(f"- 钠 **{na:.0f} mg**，约占每日建议上限的 **{pct:.0f}%**{warn}")
        L.append("")

    if insight:
        L.append("## 一句话结论")
        L.append("")
        d = insight.get("duel")
        if d:
            L.append(f"> 同样吃 **{d['base_kcal']:.0f} 大卡**：**{d['top']['name']}** 给你 "
                     f"**{d['top_protein']:.0f} g** 蛋白，**{d['bottom']['name']}** 只有 "
                     f"**{d['bottom_protein']:.0f} g**。")
            if d.get("times"):
                L.append(f"> 差了 **{d['times']} 倍**——同一个胃，装的完全不是同一种东西。")
            L.append("")
        g = insight.get("gap")
        if g:
            a, b = g["lean"], g["worst"]
            L.append(f"> 同样吃到约 **{a['protein']:.0f} g 蛋白**：**{fmt_items(a)}** 只要 "
                     f"**{a['kcal']:.0f} kcal**，**{fmt_items(b)}** 要 **{b['kcal']:.0f} kcal**，"
                     f"差的 {g['delta_kcal']:.0f} kcal 差不多是慢跑 **{g['jog_min']} 分钟**。")
            L.append("")

    if verified:
        L.append(f"> 已用麦当劳 calculate-price 接口复核实付：**¥{verified / 100.0:.2f}**")
        L.append("")

    L.append("## 单品效率榜（蛋白密度从高到低）")
    L.append("")
    L.append("| 单品 | 价格 | 热量 | 蛋白 | 每元蛋白 | 蛋白密度 |")
    L.append("|---|---|---|---|---|---|")
    for it in top_items:
        m = metrics(it)
        L.append(f"| {it['name']} | ¥{it['price']:g} | {it['kcal']:.0f} | {it['protein']:.0f}g "
                 f"| {m['g_per_yuan']} | {m['protein_density']} |")
    L.append("")
    n_s = sum(1 for x in pool if x.get("kind") == "单品")
    n_c = sum(1 for x in pool if x.get("kind") == "套餐")
    L.append(f"可算账 {len(pool)} 项：单品 {n_s} 个（菜单名与营养库精确同名），"
             f"套餐 {n_c} 个（官方默认搭配逐项相加）。对不上的不做估算。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="麦门顶饿算账")
    ap.add_argument("--city", default="周口市")
    ap.add_argument("--keyword", default="", help="门店关键词。不给的话，就列出这座城市的门店让人选")
    ap.add_argument("--store", default="", help="直接指定 storeCode，跳过门店搜索")
    ap.add_argument("--pick", type=int, default=0, help="配合 --city 用，直接选第几家（从 1 开始）")
    ap.add_argument("--budget", type=float, default=35.0)
    ap.add_argument("--kcal", type=float, default=800.0, help="0 表示不限")
    ap.add_argument("--max-items", type=int, default=3)
    ap.add_argument("--oneline", action="store_true", help="只输出一句话，方便发微信")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        client = mc.McdMcp()
    except mc.TokenMissing:
        print(TOKEN_HELP, file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"连接麦当劳接口失败：{e}", file=sys.stderr)
        return 2

    # ---------------- 定门店 ----------------
    if args.store:
        store = {"storeCode": args.store, "storeName": f"门店 {args.store}", "distance": 0}
    else:
        keyword = (args.keyword or "").strip()
        if keyword:
            stores = mc.get_stores(client, args.city, keyword)
            if not stores:
                print(f"「{keyword}」附近没搜到门店，改用整座城市再搜一次。", file=sys.stderr)
                stores = mc.get_stores(client, args.city, args.city)
                keyword = ""
        else:
            stores = mc.get_stores(client, args.city, args.city)

        if not stores:
            print(f"在「{args.city}」没搜到门店。换个城市名试试，比如 郑州市。", file=sys.stderr)
            return 3

        stores = sort_by_distance(stores)

        if keyword:
            store = choose_store(stores, keyword)
        elif args.pick:
            if not 1 <= args.pick <= len(stores):
                print(f"--pick {args.pick} 超出范围，这座城市一共 {len(stores)} 家门店。", file=sys.stderr)
                return 3
            store = stores[args.pick - 1]
        else:
            if not args.oneline:
                print(render_store_list(stores), file=sys.stderr)
                print("", file=sys.stderr)
            store, auto = prompt_pick_store(stores)
            if auto and not args.oneline:
                print(f"已自动选择最近的一家：{store.get('storeName', '')}", file=sys.stderr)

    sc = store["storeCode"]

    if not args.oneline:
        print("正在拉门店菜单、营养表和套餐组成（约 15 秒）...", file=sys.stderr)
    nutrition = mc.get_nutrition(client)
    pool, dropped = ct.collect_items(client, sc, nutrition=nutrition)
    if not pool:
        print("这家门店没有能对上营养库的餐品，换一家门店试试。", file=sys.stderr)
        return 4
    n_single = sum(1 for x in pool if x["kind"] == "单品")
    n_combo = sum(1 for x in pool if x["kind"] == "套餐")
    if not args.oneline:
        print(f"可算账 {len(pool)} 项（单品 {n_single} + 套餐 {n_combo}"
              + (f"，{len(dropped)} 个套餐因配料缺营养数据已排除" if dropped else "")
              + "）", file=sys.stderr)

    combos = enumerate_combos(pool, args.budget, args.kcal or None, args.max_items)
    if not combos:
        print(f"预算 {args.budget:g} 块、热量 {args.kcal:g} 大卡之内，凑不出含蛋白的组合，放宽一点再试。",
              file=sys.stderr)
        return 5
    tiers = pick_tiers(combos)
    insight = build_insight(combos, tiers.get("吃最饱不胖"), pool)

    # ---------------- 一句话模式 ----------------
    if args.oneline:
        print(render_oneline(store, args.budget, args.kcal, tiers, insight))
        return 0

    hint = budget_hint(pool, args.budget)

    verified = None
    best = tiers.get("吃最饱不胖")
    if best:
        try:
            payload = mc.calc_price(client, sc, [{"productCode": it["code"], "quantity": it["qty"]}
                                                 for it in best["items"]])
            verified = payload.get("price")
        except Exception as e:
            print(f"（算价复核跳过：{e}）", file=sys.stderr)

    top_items = sorted(pool, key=lambda x: metrics(x)["protein_density"], reverse=True)[:10]

    if args.json:
        print(json.dumps({"store": store, "budget": args.budget, "kcal_cap": args.kcal,
                          "tiers": tiers, "insight": insight,
                          "verified_price_fen": verified, "pool_size": len(pool)},
                         ensure_ascii=False, indent=2))
    else:
        print(render(store, args.budget, args.kcal, pool, tiers, top_items, verified, insight, hint))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
