# 03 — 数据与瓶颈实测

## A. public_set.jsonl schema

每条 session 只有 6 个字段，**agent 只能看到 `user_profile`**：

```json
{"sample_id": "public_0001",
 "scenario_type": "buying",              // 评测用，agent 看不到
 "category_bucket": "clothing",          // 200/200 全部相同 → 零信息
 "difficulty_bucket": "easy",            // 见下，零信息
 "ground_truth": {"parent_asin": "..."}, // 评测用
 "user_profile": {"average_prior_rating": 5.0,
                  "preference_tags": ["fit","comfort","durability"],
                  "purchase_frequency": "3-4 prior purchases",
                  "rating_style": "usually positive",
                  "summary": "..."}}
```

`intent_card` 与 `behavior` **不在文件里**，由 `materialize_hidden_fields()` 从目标商品
确定性地现场生成。

### difficulty_bucket 完全由 scenario_type 决定 → 零额外信息

| scenario | difficulty | n |
|---|---|---|
| buying | easy | 80 |
| browsing | medium | 80 |
| boundary | medium | 10 |
| intent_override | hard | 30 |

### user_profile 的个性化信号很弱

- `purchase_frequency`：200/200 全是 `"3-4 prior purchases"` → **常量，无用**
- `preference_tags`：只有 9 种通用标签（fit 163、material 154、comfort 144、style 101、
  durability 47、performance 26、warmth 18、weather 12、general shopping 1），
  **与目标商品无直接对应关系**
- `rating_style` / `average_prior_rating`：与目标商品的关系同样间接

→ **结论：不要在个性化上花时间。** 这是负面结论，但省下的时间可以投到排序上。

## B. intent card 结构

- **每个 session 恰好 4 条约束**（分布 `{4: 200}`）→ 印证「问两次 other 即榨干」
- 约束字符串很短：中位数 **14** 字符，均值 35.8
- 实例：`Material:alloy`、`leather`、`100% Leather`、`Imported`、`Buckle closure`、
  `Water Resistant`、`3 Year Battery`、`Day / Date Indicator`、`Stainless Steel Band`
- 其中不少极端通用（`Imported` 出现在海量 Amazon listing 里）→ **IDF 加权很重要**

### `classify_constraint` 归类分布（800 条约束）

| 归类 | 数量 |
|---|---|
| feature | 404 |
| material | 302 |
| color | 60 |
| style | 19 |
| size | 11 |
| use_case | 4 |
| **category / brand / budget** | **0** |

→ 问 `category`、`brand`、`budget` **100% 空转**。这就是固定属性轮询表现差的原因。

### override 触发轮次

`{3: 12, 4: 18}` —— 均值 3.6。

## C. 瓶颈是怎么算出来的

```
score = 0.50·HR@10 + 0.30·MRR + 0.20·Eff        Eff = clip((11 − MTTC)/10, 0, 1)
```

当前最佳配置实测：

```
HR@10 = 0.870   → 0.50 × 0.870    = 0.43500
MRR   = 0.560   → 0.30 × 0.560232 = 0.16807
MTTC  = 3.475   → Eff = (11−3.475)/10 = 0.7525 → 0.20 × 0.7525 = 0.15050
                                                          合计  = 0.75357  ✓
```

各分量上界：

| 分量 | 当前 | 上界 | 上界依据 | 最多 +score |
|---|---|---|---|---|
| HR@10 | 0.870 | 1.000 | 定义 | +0.0650 |
| MRR | 0.560 | 0.870 | **MRR ≤ HR@10**（未命中记 0，命中最多 1/1） | +0.0929 |
| Eff | 0.752 | 0.961 | 见下 | +0.0417 |

**理论最小 MTTC**：170 个非 override 会话最快第 1 轮命中；30 个 override 会话按规格
「不能在新意图发出前转化」，触发轮次实测 `{3:12, 4:18}`：

```
min MTTC = (170×1 + 12×3 + 18×4) / 200 = 278/200 = 1.390
max Eff  = (11 − 1.390)/10 = 0.9610
```

三者不可独立相加（MRR 上界随 HR 上升）。**联合上界**：HR=1、MRR=1、MTTC=1.39
→ score = 0.5 + 0.3 + 0.192 = **0.992**。

## D. 决定性测量：瓶颈 100% 在排序，不在检索

### 命中会话的 rank 分布（n=174）

| rank | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 未命中 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 数量 | 88 | 20 | 16 | 10 | 12 | 9 | 4 | 7 | 6 | 2 | 26 |

### 目标在更大 BM25 候选集中的最好位次

| N | 10 | 20 | 50 | 100 | **200** | 2000 |
|---|---|---|---|---|---|---|
| recall@N | 0.870 | 0.915 | 0.970 | 0.995 | **1.000** | 1.000 |

**200/200 个目标都能在 BM25 前 200 名内被召回，无一例外。**

当前进不了 top-10 的 26 个会话，其最好位次：11–20 有 9 个、21–50 有 11 个、
51–100 有 5 个、101–500 有 1 个。

### 结论

> **检索已经解决。剩余 0.239 的分数空间全部位于排序层。**

一个在 top-200 上完美的重排器可同时把 HR@10 → 1.000、MRR → 1.000，
score → 0.992。这把「下一步做什么」彻底确定下来了：**做重排器，别碰召回。**

## E. 断网问题的确切答案

仓库中全部相关原文（`docs/submission_rules.md`）：

> L59 — "For official final scoring, organizer policy **may** disable network access."
> L62 — "your submission must clearly document whether it requires network access"
> L63 — "if your system has an offline fallback, describe it"
> L101 — "The organizer **reserves the right** to run your submission under CPU,
> memory, timeout, and network restrictions."

**准确表述：不是确定断网，是主办方保留断网的权利。** 仓库里没有 organizer-only 文件
（Lark 题面提到的 `organizer/JUDGING_RUNBOOK.md` 等未随参赛包发布），
所以无法从代码进一步确认。

但从决策论看这是个**高度不对称的赌注**：

| | 实际断网 | 实际不断网 |
|---|---|---|
| 我们做纯本地 | 正常拿分 | 损失 = LLM 本可带来的增量（**有界**） |
| 我们依赖 API | agent 抛异常 → 规格明写 "Exceptions, invalid output, and timeouts **may count as a miss**" → **接近零分（灾难性）** | 正常拿分 |

再叠加 D 节的结论——检索已满分、剩余全在排序，而排序用廉价特征即可——
**这个保险几乎是免费的**。

→ 决定：主路径纯本地、零网络、纯 CPU；LLM 仅作可关闭的增强，附离线 fallback，
并在报告中按规则明确声明。

→ **待确认**：8/28 16:00–16:45 Track 4 workshop Q&A，直接向主办方求证。列为第一个要问的问题。
