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

INDULGE_KEYS = ("堡", "翅", "鸡块", "鸡排", "鸡腿", "派", "冰淇淋", "麦旋风", "新地")
SODIUM_DAILY = 2000.0     # 中国居民每日钠建议上限 mg
JOG_KCAL_PER_MIN = 10.0   # 60kg 慢跑约 10 kcal/分钟
MIN_CAL_PROTEIN = 15.0    # 解馋档的蛋白下限，防止选出纯糖油
MIN_ECON_PROTEIN = 10.0   # 省钱档的蛋白下限，防止把薯饼当蛋白来源


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
    price, kcal, protein = x["price"], x["kcal"], x["protein"]
    return {
        "g_per_yuan": round(protein / price, 2) if price > 0 else 0.0,
        "protein_density": round(protein / (kcal / 100.0), 1) if kcal > 0 else 0.0,
    }


def enumerate_combos(pool, budget, kcal_cap, max_items=3, max_qty=2):
    combos = []
    n = len(pool)
    for k in range(1, max_items + 1):
        for idxs in itertools.combinations(range(n), k):
            for qtys in itertools.product(range(1, max_qty + 1), repeat=k):
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
                               "price": pool[i]["price"], "qty": q} for i, q in zip(idxs, qtys)],
                    "price": round(price, 1),
                    "kcal": round(kcal),
                    "protein": round(protein, 1),
                    "fat": round(sum(pool[i]["fat"] * q for i, q in zip(idxs, qtys)), 1),
                    "carb": round(sum(pool[i]["carb"] * q for i, q in zip(idxs, qtys)), 1),
                    "sodium": round(sum(pool[i]["sodium"] * q for i, q in zip(idxs, qtys))),
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
    cand = [c for c in combos if c["protein_density"] >= 3.0]
    if cand:
        c = _pick(cand, lambda c: (c["protein"], -c["kcal"]), used)
        if c:
            tiers["吃最饱不胖"] = c

    # ② 每块钱蛋白最多：不看热量只看钱，但设蛋白下限，别把薯饼当蛋白来源
    cand2 = [c for c in combos if c["protein"] >= MIN_ECON_PROTEIN]
    if cand2:
        c = _pick(cand2, lambda c: (c["g_per_yuan"], c["protein"], -c["price"]), used)
        if c:
            tiers["每块钱蛋白最多"] = c

    # ③ 过嘴瘾还不亏：必须含炸物/汉堡，且蛋白不低于下限
    cand3 = [c for c in combos
             if c["protein"] >= MIN_CAL_PROTEIN
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


def fmt_items(combo):
    return " + ".join(f"{it['name']}×{it['qty']}" for it in combo["items"])


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
    L.append(f"可算账单品 {len(pool)} 个。其余菜单项因营养库无对应条目已排除，不做估算。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="麦门顶饿算账")
    ap.add_argument("--city", default="周口市")
    ap.add_argument("--keyword", default="万达广场")
    ap.add_argument("--store", default="")
    ap.add_argument("--budget", type=float, default=35.0)
    ap.add_argument("--kcal", type=float, default=800.0, help="0 表示不限")
    ap.add_argument("--max-items", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    client = mc.McdMcp()

    if args.store:
        store = {"storeCode": args.store, "storeName": f"门店 {args.store}"}
    else:
        stores = mc.get_stores(client, args.city, args.keyword)
        if not stores:
            print(f"没在「{args.city} {args.keyword}」附近找到门店。", file=sys.stderr)
            return 2
        store = choose_store(stores, args.keyword)
    sc = store["storeCode"]

    meals, _ = mc.get_meals(client, sc)
    nutrition = mc.get_nutrition(client)
    pool = build_pool(meals, nutrition)
    if not pool:
        print("这家门店没有单品能对上营养库。", file=sys.stderr)
        return 3

    combos = enumerate_combos(pool, args.budget, args.kcal or None, args.max_items)
    if not combos:
        print(f"预算 ¥{args.budget:g} / 热量 {args.kcal:g} 内凑不出有蛋白的组合。", file=sys.stderr)
        return 4
    tiers = pick_tiers(combos)
    insight = build_insight(combos, tiers.get("吃最饱不胖"), pool)
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
