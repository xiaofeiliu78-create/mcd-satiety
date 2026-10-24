"""餐品目录：把门店菜单变成「可算账的候选池」。

这一层负责回答一个问题：这家店里，哪些东西我们能算热量和蛋白？

两类：
  1. 单品 —— 菜单名与官方营养库**精确同名**，直接取营养。
  2. 套餐 —— 用 query-meal-detail 取官方默认搭配，把每样配料的营养相加。

对不上的，一律不进池子，不估、不猜、不外推。

web 的数据快照和命令行脚本共用这一份逻辑，避免「网页一个数、命令行另一个数」。
"""

import re

import mcd_client as mc

# 套餐识别关键词
SETMEAL_KEYWORDS = ["套餐", "三件套", "四件套", "超值", "组合", "桶",
                    "双人", "全家", "1+1", "随心选", "分享餐", "随心配"]

# 配料名 → 营养库名字的显式别名。只在语义明确等价时才加，拿不准一律不加。
# 营养库里本来就有「麦乐鸡4块」「麦乐鸡5块」两个条目，不需要任何换算。
COMPONENT_ALIAS = {
    "苹果片一袋(60g)": ("苹果片", 1),
    "苹果片一袋（60g）": ("苹果片", 1),
    "原味双层深海鳕鱼堡": ("双层深海鳕鱼堡", 1),
    "麦辣鸡翅2块": ("麦辣鸡翅-2块", 1),
    "麦辣鸡翅4块": ("麦辣鸡翅-2块", 2),
}


def norm(s):
    """归一化名字：去掉括号补充、空格、连字符。"""
    s = re.sub(r"[（(].*?[)）]", "", s)
    return s.replace(" ", "").replace("-", "")


def is_setmeal(name):
    return any(k in name for k in SETMEAL_KEYWORDS)


def build_nutrition_index(nutrition):
    idx = {}
    for name in nutrition:
        idx.setdefault(norm(name), name)
    return idx


def resolve_component(name, nut_idx, nutrition):
    """把一个配料名解析成 (营养条目名, 份数)，解析不了返回 None。"""
    key = norm(name)
    if key in nut_idx:
        return nut_idx[key], 1
    if name in COMPONENT_ALIAS:
        target, qty = COMPONENT_ALIAS[name]
        if target in nutrition:
            return target, qty
    return None


def collect_items(client, store_code, nutrition=None, nut_idx=None):
    """抓一家店的可算账候选池，返回 (items, dropped)。

    items 每项：code / name / kind / price / kcal / protein / fat / carb / sodium / image / basis
    dropped 是被排除的套餐及原因，用于对外说明「为什么这个算不了」。
    """
    if nutrition is None:
        nutrition = mc.get_nutrition(client)
    if nut_idx is None:
        nut_idx = build_nutrition_index(nutrition)

    raw = client.call("query-meals", {"storeCode": store_code, "orderType": 1, "beType": 1})
    meals = ((raw or {}).get("data") or {}).get("meals") or {}

    items, dropped = [], []

    # ---------- 1. 单品：菜单名与营养库精确同名 ----------
    for mcode, m in meals.items():
        name = m.get("name") or ""
        if is_setmeal(name):
            continue
        if name not in nutrition:
            continue
        n = nutrition[name]
        items.append({
            "code": mcode,
            "name": name,
            "kind": "单品",
            "price": float(m.get("currentPrice") or 0),
            "kcal": float(n.get("kcal") or 0),
            "protein": float(n.get("protein") or 0),
            "fat": float(n.get("fat") or 0),
            "carb": float(n.get("carb") or 0),
            "sodium": float(n.get("sodium") or 0),
            "image": m.get("image") or "",
            "basis": "官方营养表同名条目",
        })

    # ---------- 2. 套餐：官方默认搭配逐项相加 ----------
    for mcode, m in meals.items():
        name = m.get("name") or ""
        if not is_setmeal(name):
            continue
        try:
            d = client.call("query-meal-detail",
                            {"storeCode": store_code, "orderType": 1, "beType": 1, "code": mcode})
        except Exception as e:
            dropped.append({"name": name, "reason": "取组成失败：%s" % e})
            continue
        rounds = ((d or {}).get("data") or {}).get("rounds") or []
        parts, miss = [], []
        for r in rounds:
            for ch in r.get("choices", []):
                if ch.get("isDefault") != 1:
                    continue
                cname = ch.get("name") or ""
                hit = resolve_component(cname, nut_idx, nutrition)
                if hit is None:
                    miss.append(cname)
                else:
                    parts.append({"name": cname, "use": hit[0], "qty": hit[1]})
        if miss:
            dropped.append({"name": name, "reason": "配料无营养数据：" + "、".join(miss)})
            continue
        if not parts:
            dropped.append({"name": name, "reason": "无默认搭配"})
            continue

        tot = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0, "sodium": 0.0}
        for p in parts:
            n = nutrition[p["use"]]
            for k in tot:
                tot[k] += float(n.get(k) or 0) * p["qty"]

        items.append({
            "code": mcode,
            "name": name,
            "kind": "套餐",
            "price": float(m.get("currentPrice") or 0),
            "kcal": round(tot["kcal"], 1),
            "protein": round(tot["protein"], 1),
            "fat": round(tot["fat"], 1),
            "carb": round(tot["carb"], 1),
            "sodium": round(tot["sodium"], 1),
            "image": m.get("image") or "",
            "basis": "默认搭配相加：" + " + ".join(
                '%s%s' % (p["use"], "x%d" % p["qty"] if p["qty"] > 1 else "") for p in parts),
        })

    items.sort(key=lambda x: -(x["protein"] / (x["kcal"] / 100.0)) if x["kcal"] > 0 else 0)
    return items, dropped
