#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mcd_client.py — 麦当劳 MCP 轻客户端
直接按 MCP JSON-RPC 协议调用 https://mcp.mcd.cn（Streamable HTTP）。

Token 来源优先级：
  1. 环境变量 MCD_MCP_TOKEN
  2. ~/.workbuddy/mcp.json 里 mcd-mcp 的 Authorization 头（WorkBuddy 连接器配置）

被 mcd_solver.py 复用，不单独面向用户。
"""
import json
import os
import re
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.mcd.cn"
PROTOCOL = "2025-06-18"


# ---------------------------------------------------------------- token / rpc
def load_token():
    tok = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if tok:
        return tok
    cfg = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")
    if not os.path.exists(cfg):
        raise RuntimeError(
            "没找到 MCP Token。请设置环境变量 MCD_MCP_TOKEN，"
            "或在 WorkBuddy 连接器里配好 mcd-mcp。"
        )
    data = json.load(open(cfg, encoding="utf-8"))
    server = (data.get("mcpServers") or {}).get("mcd-mcp") or {}
    auth = ((server.get("headers") or {}).get("Authorization") or "").strip()
    if not auth:
        raise RuntimeError("mcp.json 里没有 mcd-mcp 的 Authorization 头。")
    return auth.replace("Bearer", "").strip()


class McdMcp:
    """极简 MCP 客户端：只用到 tools/call。"""

    def __init__(self, token=None, timeout=60):
        self.token = token or load_token()
        self.timeout = timeout
        self._id = 0
        self._initialized = False

    def _post(self, body):
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer " + self.token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise RuntimeError("MCP Token 无效或已过期（401）。请到 open.mcd.cn/mcp 重新激活。")
            if e.code == 429:
                raise RuntimeError("触发限流（429，600 次/分钟）。请降低调用频率。")
            raise RuntimeError(f"HTTP {e.code}: {detail}")
        if "data:" in raw[:30] or raw.startswith("event:"):
            raw = "".join(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
        return json.loads(raw)

    def initialize(self):
        if self._initialized:
            return
        self._id += 1
        self._post({
            "jsonrpc": "2.0", "id": self._id, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "mcd-satiety", "version": "1.0"}},
        })
        self._initialized = True

    def call(self, tool, arguments=None):
        """调用工具，返回 payload。"""
        self.initialize()
        self._id += 1
        resp = self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                           "params": {"name": tool, "arguments": arguments or {}}})
        if "error" in resp:
            raise RuntimeError(f"{tool} 调用失败: {json.dumps(resp['error'], ensure_ascii=False)[:200]}")
        result = resp.get("result") or {}
        text = "".join(c.get("text", "") for c in (result.get("content") or []))
        if result.get("isError"):
            raise RuntimeError(f"{tool} 返回错误: {text[:300]}")
        return _unwrap(text)


# ------------------------------------------------------------------- unwrap
def _unwrap(text):
    """MCP 返回的正文前面有一大段 API 文档，真正数据在末尾的 JSON 里。"""
    idx = text.find('{"success"')
    if idx < 0:
        idx = text.find('{"code"')
    if idx < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(idx, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[idx:i + 1])
                except json.JSONDecodeError:
                    return text[idx:i + 1]
    return text


# --------------------------------------------------------------- 数据解析层
def get_nutrition(client):
    """
    list-nutrition-foods 返回的是一个文本表格字符串：
      [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
        猪柳麦满分,null,1288,308,16,16,24,781,213
    解析成 {名称: {...}}
    """
    payload = client.call("list-nutrition-foods")
    table = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(table, str):
        return {}

    out = {}
    for line in table.splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        p = [x.strip() for x in line.split(",")]
        if len(p) < 9:
            continue
        try:
            out[p[0]] = {
                "kcal": float(p[3]), "protein": float(p[4]), "fat": float(p[5]),
                "carb": float(p[6]), "sodium": float(p[7]), "calcium": float(p[8]),
            }
        except ValueError:
            continue
    return out


def get_meals(client, store_code, order_type=1, be_type=1):
    """返回 {code: {name, price, original_price}} 以及分类顺序。"""
    payload = client.call("query-meals", {"storeCode": store_code,
                                          "orderType": order_type, "beType": be_type})
    data = payload.get("data") or {}
    meals = data.get("meals") or {}
    out, categories = {}, []
    for cat in data.get("categories") or []:
        names = []
        for item in cat.get("meals") or []:
            code = item.get("code")
            m = meals.get(code) or {}
            name = m.get("name")
            if not name:
                continue
            try:
                price = float(m.get("currentPrice") or 0)
            except (TypeError, ValueError):
                price = 0.0
            out[code] = {"name": name, "price": price,
                         "original_price": m.get("originalPrice")}
            names.append(name)
        categories.append({"name": (cat.get("name") or "").replace("\n", " / "), "meals": names})
    return out, categories


def get_stores(client, city, keyword, be_type=1):
    payload = client.call("query-nearby-stores", {"beType": be_type, "searchType": 2,
                                                  "city": city, "keyword": keyword})
    return payload.get("data") or []


def calc_price(client, store_code, items, order_type=1, be_type=1):
    """items: [{"productCode": code, "quantity": 1}]  返回金额单位为分。"""
    payload = client.call("calculate-price", {"storeCode": store_code, "orderType": order_type,
                                              "beType": be_type, "items": items})
    return payload.get("data") or {}
