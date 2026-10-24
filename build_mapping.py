#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 data/mapping.json —— 让 Skill 在没有 Python 时也能做名称对齐"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcd_client as mc

STORE = "3560111"
c = mc.McdMcp()
meals, _ = mc.get_meals(c, STORE)
nut = mc.get_nutrition(c)

exact = []
for code, m in meals.items():
    n = nut.get(m["name"])
    if not n or m["price"] <= 0:
        continue
    exact.append({
        "name": m["name"], "code": code, "price": m["price"],
        "kcal": n["kcal"], "protein": n["protein"], "fat": n["fat"],
        "carb": n["carb"], "sodium": n["sodium"], "calcium": n["calcium"],
    })
exact.sort(key=lambda x: -(x["protein"] / (x["kcal"] / 100) if x["kcal"] else 0))

# 记录那些「看起来像但没采用」的模糊对应，写明为什么不用
aliases = [
    {"nutrition": "中薯条", "menu": "薯条", "usable": False,
     "why": "菜单只有一个「薯条」SKU，中/大/小杯规格价格不同，强行对应会配错价"},
    {"nutrition": "大薯条", "menu": "薯条", "usable": False, "why": "同上"},
    {"nutrition": "小薯条", "menu": "薯条", "usable": False, "why": "同上"},
    {"nutrition": "麦乐鸡5块", "menu": "麦乐鸡", "usable": False,
     "why": "菜单未标明块数，无法确定单价对应 4 块还是 5 块"},
    {"nutrition": "麦乐鸡4块", "menu": "麦乐鸡", "usable": False, "why": "同上"},
    {"nutrition": "吉士汉堡包", "menu": "吉士汉堡包套餐", "usable": False,
     "why": "一边是单品一边是套餐，热量口径不同，不能混算"},
    {"nutrition": "芝士双层安格斯厚牛堡", "menu": "芝士安格斯厚牛堡", "usable": False,
     "why": "单层/双层是不同商品，热量差一倍"},
]

out = {
    "说明": "营养库(list-nutrition-foods) 与 门店菜单(query-meals) 的名称对齐表。只收录名称精确一致的项。",
    "生成时间": "2026-10-24",
    "参考门店": {"storeCode": STORE, "name": "麦当劳周口文昌万达广场餐厅"},
    "对齐规则": "营养名称 == 菜单名称，完全一致才可用。不做模糊匹配、不做估算。",
    "精确对齐": exact,
    "已排除的模糊对应": aliases,
    "注意": "不同门店菜单项不同，精确对齐的集合会变。本表是 3560111 门店的实测结果，仅供无代码模式起步使用；正式运行应以实时 query-meals 结果重新对齐。",
}

os.makedirs("data", exist_ok=True)
json.dump(out, open("data/mapping.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("已生成 data/mapping.json：精确对齐", len(exact), "项，排除模糊", len(aliases), "项")
