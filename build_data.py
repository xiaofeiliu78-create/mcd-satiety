"""生成网页演示用的数据快照：多城市多门店 + 单品 + 套餐 + 图片。

为什么要有这个脚本：
  web/index.html 是纯前端页面，不能实时调接口（会把 Token 暴露给所有访客）。
  数据只能预先抓成快照。这个脚本就是快照的唯一来源。

餐品怎么算：
  抓取逻辑全部在 catalog.py 里，命令行脚本用的是同一份代码，
  所以网页和命令行不会出现两个不同的答案。

用法：
  python build_data.py                 # 抓默认几个城市
  python build_data.py --city 周口市   # 只抓一个城市
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcd_client as mc
import catalog as ct

# 默认抓这些城市，每个城市取最近的 2 家店
DEFAULT_CITIES = ["周口市", "北京市", "上海市", "广州市", "成都市", "杭州市"]
STORES_PER_CITY = 2
MIN_ITEMS_PER_STORE = 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", action="append", help="只抓指定城市，可重复")
    ap.add_argument("--out", default="web/data.json")
    args = ap.parse_args()

    cities = args.city or DEFAULT_CITIES
    client = mc.McdMcp(mc.load_token())
    nutrition = mc.get_nutrition(client)

    stores_out, dropped_all = [], []

    for city in cities:
        rows = mc.get_stores(client, city, city)
        if not rows:
            print("[跳过] %s：没搜到门店" % city)
            continue
        for st in rows[:STORES_PER_CITY]:
            label = "%s / %s" % (city, st.get("storeName"))
            try:
                items, dropped = ct.collect_items(
                    client, st["storeCode"], nutrition=nutrition)
            except Exception as e:
                print("[失败] %s：%s" % (label, e))
                continue
            if len(items) < MIN_ITEMS_PER_STORE:
                # 多半是甜品站 / 麦咖啡这类没有正餐菜单的门店，放进下拉只会让人选到空白
                print("[跳过] %s：只有 %d 个可算项，不是正餐门店" % (label, len(items)))
                continue
            single = sum(1 for i in items if i["kind"] == "单品")
            combo = sum(1 for i in items if i["kind"] == "套餐")
            print("[OK] %s：单品 %d + 套餐 %d = %d 项，排除套餐 %d 个"
                  % (label, single, combo, len(items), len(dropped)))
            stores_out.append({
                "city": city,
                "storeCode": st.get("storeCode"),
                "name": st.get("storeName"),
                "address": st.get("address"),
                "distance": st.get("distance"),
                "items": items,
            })
            dropped_all.extend(dict(d, store=label) for d in dropped)

    payload = {
        "captured": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": "本数据由 build_data.py 通过麦当劳官方 MCP 实时抓取后固化，仅用于网页演示。"
                "要查自己附近的门店与最新价格请跑 mcd_solver.py。",
        "stores": stores_out,
        "dropped": dropped_all,
    }
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    total = sum(len(s["items"]) for s in stores_out)
    print("\n写入 %s：%d 家店，共 %d 个可算项，排除 %d 个套餐"
          % (args.out, len(stores_out), total, len(dropped_all)))


if __name__ == "__main__":
    main()
