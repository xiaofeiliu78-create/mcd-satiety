# MCP 集成说明

本文档记录本项目**真实调用**麦当劳中国 MCP 的全过程，包括接口选择、参数、踩到的坑和验证方式。所有数字均为真实运行结果，非构造数据。

## 1. 接入信息

| 项 | 值 |
|---|---|
| 接入地址 | `https://mcp.mcd.cn`（注意：**根路径**，不是 `/mcp`） |
| 传输协议 | Streamable HTTP |
| 协议版本 | `2025-06-18` |
| 鉴权 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| Token 获取 | https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 |
| 服务端标识 | `serverInfo: {"name":"mcd-mcp","version":"1.0.0"}` |
| 限流 | 600 次/分钟，超限返回 429 |

`initialize` 握手的真实返回：

```json
{"jsonrpc":"2.0","id":1,"result":{
  "capabilities":{"tools":{}},
  "protocolVersion":"2025-06-18",
  "serverInfo":{"name":"mcd-mcp","version":"1.0.0"}}}
```

本项目通过标准 MCP `tools/call` 调用，客户端实现见 `mcd_client.py`（仅用标准库，无第三方依赖）。

## 2. 用到的 4 个工具

### 2.1 `query-nearby-stores` — 定位门店

```json
{"beType": 1, "searchType": 2, "city": "周口市", "keyword": "万达广场"}
```

返回门店 `storeCode`、营业时间、可预约时段。真实返回片段：

```json
{"storeCode":"3560111","storeName":"麦当劳周口文昌万达广场餐厅",
 "address":"川汇区大庆路文昌大道万达广场","businessStatus":true,
 "businessStartTime":"07:00","businessEndTime":"22:59"}
```

> **坑 1：** 该接口按**距离**返回，**不按关键词过滤**。传 `keyword=七一路` 时，返回的第一家是离得最近的「文昌万达」而非「七一路餐厅」。
> 处理：`choose_store()` 里补了一层关键词与 `storeName + address` 的匹配打分，避免选错门店。

### 2.2 `query-meals` — 取门店真实售价

```json
{"storeCode": "3560111", "orderType": 1, "beType": 1}
```

返回 13 个分类、92 个餐品，每项含 `code` / `currentPrice` / `originalPrice`。

> **坑 2：** 返回正文前面有一大段「API Response Information」字段说明，真正的 JSON 在末尾。
> 处理：`_unwrap()` 从首个 `{"success"` 起做**括号配平**（带字符串转义处理）截取，而不是简单 split。

### 2.3 `list-nutrition-foods` — 取官方营养数据

无参数。返回 **160 条**餐品的能量、蛋白质、脂肪、碳水、钠、钙。

> **坑 3（最关键）：** 这个接口的 `data` 字段不是 JSON 数组，而是一个**用换行分隔的文本表格字符串**：
>
> ```
> [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
>   猪柳麦满分,null,1288,308,16,16,24,781,213
> ```
>
> 字段顺序为：名称, 营养描述, 能量kJ, 能量kcal, 蛋白, 脂肪, 碳水, 钠mg, 钙mg。
> 处理：先 `_unwrap()` 取出 JSON，再对 `data` 字符串逐行按逗号切分解析。若按常规 JSON 数组处理会得到 0 条数据。

### 2.4 `calculate-price` — 官方接口复核金额

```json
{"storeCode": "3560111", "orderType": 1, "beType": 1,
 "items": [{"productCode": "520445", "quantity": 2}]}
```

真实返回（节选）：

```json
{"productOriginalPrice":3100,"productPrice":3100,"originalPrice":3100,
 "discount":0,"price":3100,
 "productList":[{"productCode":"520445","productName":"薄皮焦香V翅",
   "quantity":2,"originalSubtotal":3100,"subtotal":3100}]}
```

> **坑 4：** 金额单位是**分**（`3100` = ¥31.00），展示前必须除以 100。

本项目每次输出都会调用该接口复核推荐组合的实付金额，并在结果里注明「已用 calculate-price 复核实付 ¥31.00」，这是可信度来源。

## 3. 核心工程量：把两套不通的数据缝起来

这是本项目唯一需要动脑的地方，也是它的护城河：

- `list-nutrition-foods` 有营养，**没有价格**（且是文本表格）
- `query-meals` 有价格，**没有营养**
- 两者靠**餐品名称**关联，但命名不完全一致

实测对齐结果：

| 类别 | 数量 |
|---|---|
| 营养库条目 | 160 |
| 门店菜单餐品 | 92 |
| 精确同名（可直接使用） | 19 |
| 模糊可对齐（中薯条→薯条、麦乐鸡5块→麦乐鸡等） | 21 |

**本项目只使用「精确同名」的 19 个单品**，宁可少算，不给估算数字。模糊匹配仅作为后续优化方向记录在此，未纳入计算。

## 4. 接口可用性实测（决定选题的依据）

在开发前对全部 35 个工具做了只读探测（实际工具数 35，官方文档只列 20 个）：

| 接口 | 实测结果 | 对选题的影响 |
|---|---|---|
| `list-nutrition-foods` | 160 条，可用 | 采用 |
| `query-meals` | 92 个餐品含价格，可用 | 采用 |
| `calculate-price` | 可用，返回分 | 采用 |
| `query-nearby-stores` | 可用 | 采用 |
| `query-store-coupons` | **返回空数组** | 因此放弃「用券凑单」方向 |
| `order-list` | **返回空对象**（无历史订单） | 放弃「点单复盘」方向 |
| `query-my-account` / `query-lottery-info` | **积分为 0，`resourceEligible=false`「积分不足」** | 放弃「积分抽奖」方向 |

结论：**放弃用券凑单、历史订单、积分抽奖三个方向，全部基于实测无数据，而不是因为实现难度。**

## 5. 安全边界

以下工具具有真实副作用，本项目**永不主动调用**：

| 工具 | 副作用 |
|---|---|
| `create-order` | 真实下单，产生费用 |
| `party-order-create` | 真实预约派对 |
| `mall-create-order` | 扣减积分兑换 |
| `auto-bind-coupons` | 修改用户券账户 |
| `draw-lottery` | 消耗积分抽奖 |

仅在用户显式要求、且已用 `calculate-price` 让用户确认金额之后，才可能触发下单流程。MCP Token 属于用户本人凭证，仓库中不含任何 Token。

## 6. 复现方式

```bash
# 1. 配置 Token（二选一）
export MCD_MCP_TOKEN=你的token
# 或在 ~/.workbuddy/mcp.json 里配好 mcd-mcp 连接器

# 2. 运行
python mcd_solver.py --city 周口市 --keyword 万达广场 --budget 35 --kcal 800
```

真实输出见 `docs/sample-output.md`。
