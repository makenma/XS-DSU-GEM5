# StateGraph Draw.io 图形含义

## 图形规范

### 高层分类

所有图形统分为三类，核心区分标准是**是否暂停等待 event**：

| 分类 | 图形 | 是否暂停 | 含义 |
|---|---|---|---|
| **Action** | 平行四边形、六边形 | **不暂停** | 状态机主动发出，执行完立即继续走到下一个 State |
| **State** | 椭圆/圆、直角矩形、圆角矩形 | **暂停等待 event** | 状态机在此暂停，直到收到对应 event 才跳转 |
| **SubGraph** | 菱形 | — | 子状态图容器（内含独立展开的 StateGraph） |

### 各类详细语义

#### TX / Tx + 平行四边形

```
POCQ → LinkLayer
语义：Action，enqueue/send 一个出站 CHI flit
不暂停，继续走下一节点
```

#### Flush / Write L3 Flush + 六边形

```
POCQ → SLC_SF
语义：Action，发内部操作
如果 SLC_SF 操作是同步完成，就不暂停；
如果将来有 latency/replay，就要变成 wait state
```

#### RX / Wait + 矩形（直角 / 圆角）

```
LinkLayer → POCQ
语义：State，暂停等待对应 response/data/event 到达
无数据用直角，带数据用圆角
```

#### 椭圆/圆

```
语义：State，子图入口/出口或内部汇聚点
暂停等待对应 event（如 SLC hit 返回）
```

### 文本标注的分类

| 文本包含 | 归属 | 含义 |
|---|---|---|
| `SLC` 或 `SF`（如 `SLC HIT`、`SF Miss`） | **Action** | SLC/SF 查表返回的信息，决定分支走向 |
| `DCT`（如 `If(DCT)`） | **静态配置** | 不归 State/Action，是组件无关的静态参数 |
| `Y` / `N` | **分支** | 条件判断结果 |
| 纯标题 | **标注** | 图名、图例说明 |

### 形状明细

| 图形 | draw.io 样式 | 方向 | 分类 | 语义 |
|---|---|---|---|---|
| 平行四边形 | `shape=parallelogram` | POCQ → LinkLayer | Action | 入队/发出一个出站 CHI flit，不暂停 |
| 六边形 | `shape=hexagon` | POCQ → SLC_SF | Action | 发内部操作，同步完成不暂停，有 latency 则变 State |
| 直角矩形 | `rounded=0` | LinkLayer → POCQ | State | 暂停等待无数据的响应（CompAck、ReadReceipt、SnpResp） |
| 圆角矩形 | `rounded=1` | LinkLayer → POCQ | State | 暂停等待带数据的响应（CompData、SnpRespData） |
| 菱形 | `shape=rhombus` | — | SubGraph | 子状态图容器 |
| 椭圆/圆 | `ellipse` | — | State | 入口/出口/汇聚点，暂停等待 event |
| 文本 | `text;strokeColor=none;fillColor=none` | — | 标注 | 标题、分支条件 Y/N、图例说明 |

## 实例

```
┌──────────────────────────────────────────────┐
│ ReadUnique（标题 - text）                      │
│                                              │
│   ┌─ SLC LookUp（椭圆 - 子图入口）            │
│   │                                          │
│   ├─ TXDat send（平行四边形 - 发给 LinkLayer）  │
│   │                                          │
│   ├─ MC Read（菱形 - 子图容器）                │
│   │   └─ 展开见 MCRead.drawio.xml             │
│   │                                          │
│   ├─ SnpUnique Graph（菱形 - 子图容器）        │
│   │                                          │
│   ├─ Wait CompAck（直角矩形 - 从 LinkLayer 收到）│
│   │                                          │
│   └─ Exit（椭圆 - 子图出口）                   │
└──────────────────────────────────────────────┘
```

## MCRead 子图

| 图形 | 文字 | 含义 |
|---|---|---|
| 平行四边形 | TX ReadNoSnp | 向 LinkLayer 发下游读请求 |
| 圆角矩形 | RX CompData | 等待 LinkLayer 返回 CompData（带数据） |
| 直角矩形 | Wait ReadReceipt | 等待 LinkLayer 的 ReadReceipt |
| 平行四边形 | Tx CompAck | 向 LinkLayer 发 CompAck |

> **MCRead Exit**：memory data 已经收到，entry.data 有效，可以返回父图继续 TXDat send。

## SnpUnique 子图

| 图形 | 文字 | 含义 |
|---|---|---|
| 平行四边形 | Tx SnpUnique | 向 LinkLayer 发 SnpUnique |
| 直角矩形 | SnpResp_I | 等待 LinkLayer 的 SnpResp（无数据） |
| 圆角矩形 | SnpRespData(Ptl)_I_PD | 等待 LinkLayer 的 SnpRespData（带数据） |
| 六边形 | Flush SF | 发给 SLC_SF 刷 SF |
| 六边形 | Write L3 Flush SF | 发给 SLC_SF 写 L3 并刷 SF |

> **SnpUnique Exit**：snoop response 已处理，SF/L3 已更新完成。

## GraphSpec 生成

`drawio_to_pocq.py` 可以把本目录下的 `*.drawio.xml` 转成一个 POCQ
GraphSpec：

```sh
python3 src/mem/cache/CHI/StateGraph/drawio_to_pocq.py
```

脚本会输出两层信息：

| 字段 | 含义 |
|---|---|
| `nodes` / `edges` | draw.io 原始节点和边的分类结果，用于检查图是否被正确识别 |
| `transitions` | 折叠后的 POCQ 状态迁移；Action 节点会被折进 `actions`，等待节点会成为 `from/to` state |

例如：

```text
SLC LookUp --SlcLookupDone[slc_hit] / QueueCompData--> Wait CompAck
```

表示 `SLC LookUp` 等到 lookup 返回且命中后，POCQ enqueue `CompData`，
然后进入 `Wait CompAck` 等待 RNF 回 `CompAck`。

当前默认不启用 DCT，因此 `SnpUniqueFwd` 分支会被忽略。如需保留该分支：

```sh
python3 src/mem/cache/CHI/StateGraph/drawio_to_pocq.py --enable-dct
```
