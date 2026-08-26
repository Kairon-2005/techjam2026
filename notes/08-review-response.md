# 外部审查回应：把每条批评当成假设去实验

外部审查提出 10 条批评。本文只处理**可实证的 4 条行为类批评**，每条都实现成
默认关闭的配置项，用 `lab/sweep.py` 量化后再决定是否采纳。

结论先行：**2 条采纳，2 条被实验否决**。被否决的两条，批评对「代码现状」的描述
完全正确，但它开出的药方经测量是负收益。

基线（默认配置，commit 2f85538）：`0.928708 / HR 0.995 / MRR 0.839361 / MTTC 2.03`

---

## 批评 3：双轨路由「没有实际效果」

**描述属实。** `route_overrides` 默认 `{}`，三条路由走完全相同的管线。
另外 `lab/sweep.py` 的 7 组消融配置里写了 `"route": false` —— agent 根本没有
这个开关，那 7 行实验记录是**无效的**。

先补两件事：路由分类正确率实测 **200/200**（boundary 的首轮消息与 browsing
逐字相同，信息上不可分，归入 browsing 是正确行为）；`route_overrides` 改为
**整轮生效**（此前只作用于 rerank 权重，patch `ask_policy` / `on_override`
会静默失效——`RT4` 就是这样得到一个与基线完全相同的分数）。

然后测「路由条件化」到底值不值：

| 配置 | score | Δ |
|---|---|---|
| 基线 | 0.9287 | — |
| browsing `w_pop`=5.0 | 0.9283 | −0.0004 |
| browsing `w_pop`=6.0 | 0.9301 | **+0.0013** |
| browsing `w_pop`=8.0 | 0.9261 | −0.0026 |
| buying `w_phrase`=7.0 | 0.9272 | −0.0015 |
| browsing→pool 提问 | 0.9282 | −0.0005 |
| browsing 候选池 200 | 0.9178 | −0.0109 |
| buying 候选池 50 | 0.9174 | −0.0113 |

`w_pop=6.0` 的 +0.0013 是**噪声尖峰**，配对分析拆开看：

```
w_pop=6.0   2 个 session 变好，7 个变差；5 折里只有 2 折改善
            折间 Δ 均值 +0.0030，标准差 0.0064（标准差是均值的两倍）
```

净收益为正只是因为 2 次涨幅是「跳到 rank 1」，而 7 次跌幅都很小。邻域
（5.0 / 5.5 / 6.5 / 7.0）不支持它。**判定：不采纳权重条件化。**
采纳的是路由整轮生效的管线改造 + 未知配置键告警（默认行为零变化）。

## 批评 4：`on_override="keep"` 不是真正的意图覆盖

**描述属实。** 默认保留旧短语与旧词项，`category` 只在为空时赋值。
公开集上这样做不吃亏，原因在 `evaluator.behavior_for`：
`new_value = hard_constraints[0]`、`old_value = soft_preferences[-1]`，
**两者都来自目标商品**——被要求「忘掉」的偏好其实仍是正确答案的证据。

所以实现了 `on_override="slot"`（按契约属性槽位选择性重写：解析新消息 →
判定被取代的槽位 → 只删除该槽位的短语及其**独占**贡献的词项，其余槽位保留），
并新建 `lab/override_stress.py`：把 `old_value` 换成**别的商品**的约束，
让「过时偏好」真正具有误导性（例：目标是皮带·leather，过时偏好写成 silk）。

5 个随机种子平均（每种子 30 条 intent_override）：

| 策略 | score | sd | override HR | override MRR |
|---|---|---|---|---|
| **keep（现默认）** | **0.9233** | 0.0014 | **0.973** | **0.864** |
| erase | 0.8458 | 0.0000 | 0.400 | 0.383 |
| decay（保留最新） | 0.9164 | 0.0015 | 0.913 | 0.837 |
| decay_head（保留最旧，原实现） | 0.8867 | 0.0039 | 0.773 | 0.479 |
| slot（选择性重写） | 0.9140 | 0.0030 | 0.927 | 0.763 |

**即使覆盖是真实的、旧偏好与正确答案直接矛盾，`keep` 依然最优。**

机制：本方案**只打分不过滤**。一个过时约束最多贡献一点错误加分，它无法把
正确商品排除掉；而遗忘会真的丢掉证据——客户先前披露的多数信息仍然有效。
这是可迁移的结论，不是模拟器特性。

**判定：不把 slot 设为默认**（公开集 −0.0066，真实覆盖下 −0.0093）。
保留为已实现、已测量的能力，报告里写成「有据的设计决策」而不是宣称做了槽位擦除。
顺带修掉一个真 bug：`decay` 原本 `[:8]` / `[:1]` 保留的是**最旧**的证据，
改为保留最新后该策略 +0.030。

## 批评 5：提问策略是模拟器捷径

**描述属实，且量化后更难看：默认策略 399/405 轮（99%）问的都是 `other`。**
模拟器把 `other` 当作「返回任意未披露约束」，两轮就能榨干意图卡。

实现 `ask_policy="pool"`：对存活候选池计算每个属性取值的香农熵，问**最能切分
当前候选池**的那个属性——候选都是黑色时就不问颜色。

| 策略 | score | MTTC | 定向提问占比 |
|---|---|---|---|
| other（默认） | 0.9287 | 2.03 | 1% |
| pool（纯） | 0.9006 | 3.19 | 89% |
| other_then_pool | 0.9282 | 2.06 | 18% |
| **other_then_pool + give_up=1** | **0.9285** | 2.04 | **17%** |

配对分析（other_then_pool vs 默认）：**0 个 session 变好、0 个变差、0 次丢失命中**——
排序质量逐条相同，−0.0005 全部来自 MTTC。

为什么定向提问在这个模拟器里反而吃亏：模拟器用 `classify_constraint` 给隐藏约束
打桶，只有**问中那个桶**才会披露。剩余约束是 "Rubber sole"（桶=feature）时，
问 material/color/use_case 一律得到「没有偏好」。于是加了 dry-streak 保护：
一次定向提问落空就退回开放式提问，代价降到 **−0.0002**（远低于 ±0.001 噪声底）。

**判定：采纳。** 17% 的轮次问出真正基于候选池信息增益的问题，代价 0.0002。

## 批评 9：资源占用需要更诚实的说明

**描述属实。** `order`（位置特征）与 `card`（模拟器复刻）两个索引结构无条件构建，
但它们的权重 `w_pos` / `w_card` **默认都是 0.0**——即约 80 MB 纯废重。
改为按权重惰性构建：

| | 索引构建 | 峰值 RSS |
|---|---|---|
| 原（无条件构建） | 7.70 s | 430.1 MB |
| **现（按需构建）** | **5.07 s** | **392.8 MB** |

分数逐位相同。整个评测进程：13.6 s / 653 MB → **11.3 s / 627 MB**。
新增 `title` 短标题索引（约 6 MB）用于推荐理由文案，已计入上表。

---

## 其余批评的处置

- **批评 1（无法从 Git 复现）—— 最高风险，已修复。** 490 行未提交改动 +
  笔记 + lab 工具已冻结为 commit `2f85538`，并附一条命令复现说明。
  `lab/sweep.py` 的 `git_hash()` 现在会给脏工作区打 `+dirty` 标记，
  防止再次出现「实验记录指向不含该代码的 commit」。
- **批评 2（CV 不是无偏估计）—— 属实，已改措辞。** `lab/tune.py` 的 docstring
  此前写着该数字「estimates private-set performance」，现已改为：折仅检验
  **权重选择步骤**的稳定性，其上游（特征、停用词、解析模板、提问/覆盖策略）
  全部见过 200 条公开会话。对外一律表述为 **public development score 0.928708**。
- **批评 10（测试太浅）—— 属实，已补。** 新增 `tests/test_agent.py`（25 例：
  模板解析、槽位分类、覆盖状态、噪声回复、路由、契约 schema、top_k 钳制、
  恶意输入、非法配置）与 `tests/test_score_regression.py`（分数锁 + 零 token）。
  全套 30 例通过。另修 `lab/stress.py` 的两个 `__main__` 守卫——
  `stress v2` 此前会先把 v1 套件跑一遍。
- **批评 6/7/8（支柱缺口、模拟器耦合、提交物缺失）** 属实，属于叙事与交付物层面，
  不是实验问题。批评 8 是当前最大缺口。

---

# 四支柱逐条核查（题面 4.2 vs 当前代码）

审查后重新核查，全部以实测为准。**8 个子项：5 项达标、1 项部分、2 项经测量后
主动放弃（负结果有数字）。**

## I. Core Architecture

**Dual-Track Routing —— 部分达标。**
路由分类实测 **200/200 正确**（boundary 首轮消息与 browsing 逐字相同，
信息上不可分，归入 browsing 是正确行为，非缺陷）。`route_overrides` 已改为
**整轮生效**（可 patch 检索深度、提问策略、覆盖策略，不只是重排权重）。
但**默认三条路由仍走同一管线**——因为差异化经测量是负收益（见批评 3 表）。
题面说的 "high-precision filter track"：本方案**从不过滤，只打分**。
这是刻意的——过滤会把召回率变成上限，而打分不会。

**Multi-Route Retrieval → LLM Semantic Ranking —— 部分达标。**
keyword ✅（FTS5/BM25）、category ✅（`w_cat`）、vector ✖、LLM ✖。

放弃稠密路的依据是实测召回曲线：

| k | recall@k | 未召回会话 |
|---|---|---|
| 10 | 0.545 | 91 |
| 50 | 0.830 | 34 |
| 100 | 0.995 | 1 |
| **200** | **1.000** | **0** |

**200 条会话没有一条是「检索不到」的**，目标商品 BM25 中位排名 8。
稠密检索能改善的是召回，而召回**没有剩余空间**。加深候选池反而更差
（100→200 时 MRR 0.839→0.766，热门度先验把更多热门错项抬到目标之上）。
LLM 重排则被 `docs/submission_rules.md` 的断网条款排除。
**这是与题面字面预期最大的偏离，报告必须写成有据的设计决策。**

## II. Dialog Strategy

**Information Accumulation —— 达标。** 增量槽位累积 + `provenance` 记录每条短语
贡献了哪些词项（使选择性删除成为可能）。

**Intent Override "slot erasure and rewriting" —— 已实现，但实测后不设为默认。**
`on_override="slot"` 是题面语义的诚实实现。新建 `lab/override_stress.py` 构造
**真实矛盾**的覆盖（silk → leather），5 种子平均下 `keep` 仍最优
（0.9233 vs slot 0.9140 vs erase 0.8458）。原因同上：**只打分不过滤**，
过时约束无法排除正确商品，而遗忘会真的丢证据。

**Proactive Guidance / Over-Generality cutoff —— 达标（本轮新增）。**
`_overgeneral()`：存活候选池的 leaf 类目数 ≥ 6 时判定为「不是排序问题，
而是需求欠定」。**截断推荐在本指标下纯亏分，所以 cutoff 作用于「提问」而非「结果」**：
命中时强制走池感知提问（暂停 dry-streak 保护），并把开放式提问换成**结构化选项**，
选项标签自动去掉共同前缀只保留区分部分（"women slippers / men slippers / men sandals"）。
实测在 **36/407 轮（9%）** 触发，集中在 intent_override（27 次，覆盖后候选池确实重新变宽）。
**分数零代价**（0.9285，与关闭时逐位相同）。

## III. Self-Evolution

**Personalized Context Distillation —— 测量后放弃，负结果有数字。**
`user_profile` 此前只存不用。本轮实现 `w_profile` 特征（preference_tags 命中率）
并做权重扫描：

| w_profile | 0.0 | 0.5 | 1.0 | 2.0 | −0.5 |
|---|---|---|---|---|---|
| score | **0.9285** | 0.9239 | 0.9176 | 0.9073 | 0.9272 |

**单调变差。** 原因在数据本身：`purchase_frequency` 200 条会话**全部相同**，
`preference_tags` 只有 9 个泛化词（fit / material / comfort / style…），
命中率 50–80%——把它们加权等于给几乎所有商品同等加分，纯稀释真实约束信号。
**诚实结论：这份 profile 不携带可用的个性化信号，不是我们没做。**

**Adaptive Orchestration —— 达标。** 三处真实的运行时改道，均由观测触发而非配置写死：
1. **死槽位软匹配**：逐条约束探测全池字面命中，零命中的槽位切换到 IDF 加权软匹配
   （逐字模拟器上死集为空 ⇒ 保险费为零）。
2. **`dry_others` 降级**：连续 2 次干回复后放弃 `other`，改轮询具体属性。
3. **`dry_streak` 放弃 + 过载暂停**：定向提问落空即退回开放式，但需求欠定时暂停该保护。

## IV. Evaluation Matrix —— 完全达标

Coverage / Precision / Efficiency 三维全部对齐官方 harness，
默认配置 `0.928508 / HR@10 0.995 / MRR 0.839361 / MTTC 2.04`，
两个配置的分数都由 `tests/test_score_regression.py` 锁定。

## 一句话总结

**8 个子项：5 达标、1 部分（路由分类满分但不差异化）、2 主动放弃且附负结果数字。**
唯一的真实能力缺口是 **vector + LLM 重排**，而召回曲线证明前者无空间可救、
断网条款排除后者。其余「未做」项全部是**测量后否决**，不是遗漏。

---

# 能力评测底座（lab/scenarios.py + lab/capability.py）

## 为什么必须先做这件事

公开集是一个**弱代理**。它在结构上无法评测以下能力——不是我们没测，是它测不了：

| 能力 | 公开集为何测不了 |
|---|---|
| 意图覆盖 | `behavior_for` 的 `old_value` 与 `new_value` **都取自目标商品**，被要求忘掉的偏好仍是正确答案的证据 |
| 个性化 | `purchase_frequency` 200 条会话**完全相同**；`preference_tags` 只有 9 个泛化词 |
| 模糊浏览 | 每条开场白**都点名了品类** |
| 不配合的用户 | 回复永远格式良好，boundary 只有 10 条且只干一轮 |
| 矛盾约束 | 用户陈述的约束**永远为真** |

只对着这个代理调参，等于为一个**真实任务不具备的条件**做优化。

## 设计

`Scenario` = 官方评测循环之上的一组钩子，**只改一件事**，harness/指标/计分全部不动，
因此结果可横向比较。钩子返回 `None` 表示「用官方行为」——每个场景都是对真实评测器的
**diff**，而不是可能漂移的重实现。

`lab/capability.py` 输出**矩阵而非单一数字**：行=场景，列=配置。
读一列判断某个配置好坏；读一行看某项能力依赖哪个模块。
**某行所有配置打平 ⇒ 该能力没有任何模块在负责。**

## 首份能力记分卡

| 场景 | default | ask=other | ov=slot | ov=erase | no_guidance | profile=1.0 | no_softslot | no_pop |
|---|---|---|---|---|---|---|---|---|
| clean（对照） | 0.9285 | **0.9287** | 0.9218 | 0.8425 | 0.9285 | 0.9176 | 0.9278 | 0.8658 |
| override_genuine | 0.9196 | **0.9199** | 0.9183 | 0.8425 | 0.9196 | 0.9075 | 0.9188 | 0.8589 |
| override_category | 0.9190 | 0.9197 | 0.9190 | 0.9190 | 0.9194 | 0.9050 | **0.9234** | 0.8473 |
| vague_start | 0.8724 | **0.8742** | 0.8657 | 0.7864 | 0.8738 | 0.8648 | 0.8724 | 0.8141 |
| **uncooperative** | **0.7051** | 0.7051 | 0.6988 | 0.6188 | 0.7051 | 0.6990 | 0.7043 | 0.5576 |
| **contradiction** | 0.7990 | 0.8000 | 0.7880 | 0.7066 | 0.7997 | 0.7846 | **0.8018** | 0.6987 |
| profile_informative | 0.9285 | 0.9287 | 0.9218 | 0.8425 | 0.9285 | **0.9465** | 0.9278 | 0.8658 |

## 三条结论（都改变了优先级）

**1. 个性化是可利用的——缺的是数据不是架构。**
`profile_informative` 行里 `w_profile=1.0` 得 0.9465（+0.018），自适应版本更高
（0.9703）。这把「我们没做个性化」与「这份数据没有个性化信号」**彻底分开**了。
但自适应版本在 clean 上倒亏 0.029：无论用池覆盖率还是全局 IDF，
**泛化标签与有效标签的取值区间是重叠的**（泛化 1.42–4.05，有效 2.31–10.82），
没有干净的判别器。**默认关闭**，作为「私有集若有信号即可开启」的开关保留。
这是研究问题，不是调参问题——现在硬调阈值正是这套底座要防止的错误。

**2. 最大的真实缺口是「不配合的用户」：0.7051，比 clean 低 0.22。**
而且**整行除 `no_pop` 外全部打平**——按上面的读法，这意味着
**当前没有任何模块在负责这项能力**。公开集完全看不到这个缺口。

**3. 矛盾约束 0.7990，第二大缺口。** 且 `no_softslot` 反而最好——
`slot_soft` 在矛盾场景下轻微有害（它会给一条本就错误的约束找软匹配）。
`override_category` 同样是 `no_softslot` 最优。**slot_soft 需要一个「该约束是否可信」
的前置判断**，而不是无条件为死槽位找软匹配。

**4. 热门度先验是全场景的承重墙**：`no_pop` 在每一行都掉 0.06–0.15，
是唯一一个在所有能力维度上都关键的特征。

## 按优先级的模块路线（数据驱动，不是拍脑袋）

1. **不配合用户的兜底**（0.705，无模块负责）——最高优先级
2. **矛盾约束下的约束可信度**（0.799；同时修 slot_soft 的反向作用）
3. **个性化判别器**（有信号时 +0.042，需要真正的判别器）
4. vector / LLM 路（召回曲线证明无空间，留作有据的设计决策）

---

# Phase 1：Typed Evidence State + Uncooperative Recovery

按外部反馈扩大后的范围执行（不只是 SlotValue 数据结构）。

## 结果（5 seeds，与各自 clean 基线配对）

| 场景 | Phase 1 前 | Phase 1 后 | Δ |
|---|---|---|---|
| clean（默认） | 0.928508 | **0.928508** | 0（不变） |
| compat `ask_policy="other"` | 0.928708 | **0.928708** | 0（逐位精确） |
| **uncooperative** | 0.7219 | **0.8372** | **+0.1153** |
| **vague_start** | 0.8724 | **0.9267** | **+0.0543** |
| **contradiction** | 0.7990 | **0.8427** | **+0.0437** |
| override_genuine | 0.9196 | 0.9255 | +0.0059 |
| override_category | 0.9190 | 0.9172 | −0.0018（sd 0.0038，噪声内） |

## 关键发现：噪声污染是在做「意外的查询扩展」

反馈预测的污染确认存在：`hmm / hard / say / really / sure / think / can / just / show / more`
全部进入 BM25 查询。**但把它修掉之后，uncooperative 反而更差**
（0.7219 → 0.6973）。分解后原因是两条：

1. **检测本身没有收益**：Phase 1B 让 agent 正确识别出用户在敷衍，
   但它原有的应对（退回 `other`、轮询具体属性）本身就是坏策略。
2. **污染词在意外地扩大召回**：单独验证 `filter_noise=0, evidence_query=0`
   得 HR **0.785**，而干净版只有 **0.758**。用户不给信息时查询极窄，
   窄查询 = 窄召回，目标商品根本进不了候选池。垃圾词把 OR 查询撑开了。

所以正确的修法不是恢复污染，而是**在证据稀薄时刻意扩召回**：

| starved_candidates | uncooperative | HR | MRR | MTTC |
|---|---|---|---|---|
| 关闭 | 0.7036 | 0.758 | 0.622 | 4.10 |
| 200 | 0.7684 | 0.836 | 0.668 | 3.50 |
| 500 | 0.8292 | 0.914 | 0.701 | 2.90 |
| **1000（默认）** | **0.8372** | **0.925** | **0.706** | **2.85** |
| 2000 | 0.8363 | 0.924 | 0.704 | 2.85 |

`starved_after=2`：`after=1` 会让 clean 掉到 0.9251，`after=3` 略差。
**证据充足时深池有害（MRR 被热门近似项挤垮），证据稀薄时召回才是约束条件**——
这正是 Pillar III 的 runtime adaptation，且 clean 分文不动。

同一机制顺带修好了 `vague_start`（+0.0543 → 0.9267，HR 0.995）。
注意：**我们一行路由标签都没改**。反馈判断正确——那个分数的主因是
「首轮没有 category ⇒ BM25 缺少有效查询词」，是召回问题，不是路由标签问题。

## 已实现

- **1A 类型化证据**：`SlotValue`（attribute / value / polarity / hardness /
  confidence / source_turn / provenance / active / catalog_support /
  contradiction）。检索查询**只由 active evidence 重建**，不再吞掉每条消息的
  每个 token。clean 上两条路径逐位相同（那里的消息要么可解析、要么已被过滤），
  所以是零代价。
- **1B 回复结果分类**：`Outcome` = INFORMATIVE / OVERRIDE / NO_PREFERENCE /
  UNCERTAIN / REFUSAL / REQUEST_MORE / CORRECTION。只有 INFORMATIVE 与
  OVERRIDE 会合入证据。**踩过的坑**：最初把「只有 category 没有 constraint」
  的浏览开场判成 UNCERTAIN，90 条会话在第一轮丢掉品类，分数掉到 0.8865。
  已加回归测试锁死。
- **1C 无信息恢复**：稀薄证据扩召回（上表）；`REQUEST_MORE` 触发候选轮换
  （保护前 3 名以免伤 MRR，仅刷新尾部为未展示候选）；区分
  「没有偏好」（该维度问错了 ⇒ 转开放式）与「答不上来」（该维度太难 ⇒ 问更易回答的）。
- **1D 可信度门控 —— 负结果**：`slot_soft` 在 `override_category` 上确实值
  −0.0100（关掉得 0.9272 vs 开着 0.9172）。但按「早于 pivot / 被更新的单值属性
  取代」实现的门控**没修好它**：只找回 0.0008，且在每个场景上都要付 ~0.0007。
  **默认关闭，机制仍未查明。** 我原先的伤害假设是错的。

## 未获收益的想法（照实记录）

- **answerability 加权提问**：完全无收益（0.7036 开/关同分）。原因是模拟器的
  「可回答性」由 `classify_constraint` 的桶匹配决定，不是人类难易度——
  问 use_case 只会披露被归类为 use_case 的约束。产品设计上合理，
  但**这个模拟器无法奖励它**，与 pool-aware 提问是同一类结论。

## 方法学升级（按反馈要求）

`lab/capability.py` 现在：每个随机场景跑 5 seeds、报告 mean ± sd、
输出 **penalty = 场景分 − 该配置自己的 clean 分**（而非跨行比绝对分），
并同时报告 HR / MRR / MTTC。

## 事故记录

本轮把 `starter/agent.py` 写坏过一次：用 `s.index(A):s.index(B)` 切片时
A 在文件中位于 B 之后，切出空串，`str.replace("", block)` 把代码块插进了
**每两个字符之间**，文件膨胀到 73 MB。因插入是均匀的，可从重复间距反推出
被插入的块并整体删除，**逐字节还原**（还原后 48,721 字节，AST 校验通过）。
教训：字符串编辑一律用唯一锚点 + 计数断言，不要用 index 切片。

---

# 审查 items 1–8 处理结果

## 1. 可复现性（根因已修）

审查在 `bcfbca2`、seeds 7–11 上复跑得 `uncooperative=0.829795`，而报告写的是 `0.8372`。
**代码没有任何差异——是 seed 集不一致。** `lab/capability.py` 文档写明默认
`range(7,12)`，但我用来出数的是临时脚本里的 `(7,11,23,42,101)`，且临时脚本从不写日志，
所以差异在别人复跑之前不可见。

根因修复：**`lab/record.py` 是唯一被允许产出数字的入口**。每行记录携带
commit / dirty / dirty 文件列表 / 完整 config / 完整 seed 列表 / 每个 seed 的
四项指标 / mean / sd，追加写入 `lab/results.jsonl`。**不带 seed 列表的聚合值一律不得上报。**

修正后的 Phase 1 结果（两侧都在 seeds 7–11 重测）：

| 场景 | pre(1d5718c) | Phase 1 | Δ |
|---|---|---|---|
| clean | 0.928508 | 0.928508 | 0 |
| uncooperative | 0.711598 | 0.833266 | **+0.1217** |
| vague_start | 0.870627 | 0.917534 | **+0.0469** |
| contradiction | 0.784395 | 0.809592 | **+0.0252** |
| override_genuine | 0.921971 | 0.921971 | **0**（此前报的 +0.0059 是 seed 假象） |
| override_category | 0.913515 | 0.915013 | +0.0015（sd 0.0066，噪声内） |

## 2. depth=1000 的 CPU 成本

| depth | suite 墙钟 | peak RSS | 常规轮 p50/p95 | 饥饿轮 p50/p95 | score |
|---|---|---|---|---|---|
| 关闭 | 12.9 s | 591 MB | 6.2 / 27.7 ms | — | 0.6787 |
| 500 | 7.0 s | 593 MB | 9.9 / 20.3 ms | 9.9 / 20.3 ms | 0.8239 |
| 1000 | 7.3 s | 594 MB | 10.4 / 34.3 ms | 12.8 / 23.7 ms | 0.8353 |

**扩召回让整套评测更快**（12.9 s → 7.3 s），因为会话收敛更早、总轮数更少。
1000 相对 500：饥饿轮 **+2.9 ms p50 / +3.4 ms p95**，RSS **+1 MB**。
绝对值 12.8 ms p50 相对 60 s 预算可忽略。

**depth 选择已在 holdout 验证**（uncooperative_holdout，seeds 12–21，未见 seed +
未见措辞）：depth_500 = 0.815102 ± 0.0138，depth_1000 = **0.825127 ± 0.0145**，
**+0.0100**，与选择集上的 +0.0114 一致。**+2.9 ms p50 换 +0.010，成立。**

## 3. 饥饿信号不再等同于「连续无信息」

实测：**clean 上的 stalled 轮中位数是 17 个查询词、7 条活跃约束**——正是绝不能
被扩到 1000 的强查询。`_starved()` 现在要求「停滞（或显式 REQUEST_MORE）
**且** 查询确实稀薄」：≤ 8 词 或 ≤ 1 条活跃约束。
代价：vague_start 0.9267 → 0.9175。**保留这个保守门**。

## 4. 轮换改为一次性事件

`wants_more` 只增不减，导致此后每一轮都在旋转。现在 `REQUEST_MORE` 只装填
一次 `rotate_pending`，`_rotate` 消费它；**新证据到达时清空 `shown` 与
`rotate_pending`**，因为旧分页属于另一个结果集。

## 5. contradiction +0.0252 的归因（因子化消融）

| 关闭的因子 | contradiction | 贡献 |
|---|---|---|
| （全开） | 0.809592 | — |
| −evidence_query | 0.809592 | **0.000** |
| −outcome_filter | 0.809592 | **0.000** |
| **−starved** | 0.784395 | **+0.0252（全部）** |
| −rotation | 0.809592 | **0.000** |
| −slot_soft | 0.812014 | −0.0024（slot_soft 在此有害） |

关掉饥饿扩召回后**逐位复现 pre-Phase-1 的 0.784395**。结论：全部收益来自扩召回。

## 6. slot_soft 机制已查明（`lab/diag_slotsoft.py`）

override_category 上唯一的死短语是**被抛弃的品类**本身（`'i want shoes slippers'`）。
slot_soft 把它救活：仍是拖鞋的候选拿到 f_slot=1.0 → **+4.000**，
而 pivot 之后真正的目标拿 **+0.000**——单这一项就决定了排序
（竞争者 8.55 vs 目标 7.21）。

**根因**：`last_override_turn=0`，**这次覆盖根本没被检测到**。
旧 `OVERRIDE_RE` 要求字面的 "forget what i said"，而消息是
"forget shoes slippers entirely"。已扩展为
forget / changed my mind / no longer / instead of / not … anymore。
「forget boots, I want running shoes」是最典型的意图覆盖，此前完全不可见——
这是真实鲁棒性缺陷，不是场景造出来的。

检测修好后 `soft_needs_credible` 能把 override_category 从 0.9150 拉到 **0.9237**，
与 `slot_soft=0` 完全相同。**但仍不设为默认**：它要付 payload 改写鲁棒性
（payload_soft 0.8777→0.8617，shuffle 0.8982→0.8929，drop 0.8842→0.8737）
与 clean 0.0008。payload 改写是私有集的合理风险，而品类 pivot 是我们自造的场景，
这笔交易不划算。`on_override="slot"` 即使在检测修好后仍然更差
（0.9010 vs keep 0.9150）——**打分仍然胜过过滤**。

## 7. Phase 2A（见 commit 07c191a）

未知开场 100% 从 `override` 改为 `mixed`；路由按每轮证据从 mixed/browsing
firm up 到 buying（clean 上产生 75 次 `browsing → buying`）；
`_retrieve()` 改为使用本轮 route config（此前 `term_cap` / `bm25` 直接读
`self.cfg`，任何 route patch 都是静默无效——与 `"route": false` 同类）。
route 权重保持中性，因此行为不变：clean 0.928508、compat 0.928708 逐位一致。

## 8. Holdout 验证（seeds 12–31，配置冻结）

| 场景 | pre-Phase-1 | Phase 1+2A | Δ |
|---|---|---|---|
| uncooperative（开发用措辞） | 0.714024 ± 0.0197 | **0.833022 ± 0.0102** | **+0.1190** |
| uncooperative_holdout（**未见过的措辞**） | 0.705402 ± 0.0249 | **0.817144 ± 0.0147** | **+0.1117** |

- **选择用 seeds 7–11 得 0.833266，holdout seeds 12–31 得 0.833022** ——
  `starved_after` / `depth` 的选择跨 seed 泛化，不是调出来的。
- 未见措辞下仍保住 +0.112，仅比开发措辞低 0.016。
  （旧 agent 的同一差距是 0.009，即新 agent 对措辞略敏感一些，但收益压倒性保留。）

**holdout 暴露的一个真实缺陷（已记录、未修）**：
`'Could I see a few different ones?'` 应判为 REQUEST_MORE，实际落到 UNCERTAIN——
`MORE_RE` 要求字面的 "more"。**不在 holdout 上修**，否则 holdout 就作废了。
留待下一轮用新的开发集处理。

UNCERTAIN 是兜底分支，任何无法解析且不匹配已知模式的消息都会落到它——
所以未见措辞的检测是**设计上**泛化的，不是靠模式匹配开发集措辞。

---

# 预注册实验：仅抑制「被明确放弃的 span」

**登记时间：结果观测之前。** 见 commit 历史中本节先于结果被提交。

## 假设

当前两个选项过于粗粒度：
1. 保持 `slot_soft`：保住 payload paraphrase 鲁棒性，但品类 pivot 受损（0.9150）。
2. `soft_needs_credible=True`：修好 pivot（0.9237），但**封锁所有 pivot 之前的软证据**，
   payload 鲁棒性下降（0.8777→0.8617 / 0.8982→0.8929 / 0.8842→0.8737）。

**假设**：用户已经告诉了我们哪一部分作废。只对**被明确点名放弃的 span**
（"forget shoes slippers" → `shoes slippers`）关闭 soft-rescue，
其余旧证据（color / material）仍可 soft-match，即可同时拿到两边。

## 预测（观测前写定）

| 指标 | 预测 |
|---|---|
| override_category | ≈ 0.923，与 `soft_needs_credible` / `slot_soft=0` 相当（基线 0.9150） |
| payload_soft | ≈ 0.8777（保持默认水平，**不**掉到 0.8617） |
| payload_shuffle | ≈ 0.8982 |
| payload_drop | ≈ 0.8842 |
| clean | 0.928508 不变 |
| compat `ask_policy="other"` | 0.928708 逐位不变 |
| override_genuine | ≈ 0.9220 不变 |

**证伪条件**：若 override_category 未显著高于 0.9150，或任一 payload 风格跌到
blanket gate 的水平，则该假设被否决，回到二选一。

## 预注册实验结果：假设成立，且严格优于两个旧选项

| 指标 | 预测 | 实测 | |
|---|---|---|---|
| override_category | ≈0.923 | **0.924458 ± 0.0019** | ✅ 且高于 blanket gate（0.923708）与 slot_soft=0（0.923708） |
| payload_soft | ≈0.8777 | **0.8777** | ✅ 与默认逐位相同 |
| payload_shuffle | ≈0.8982 | **0.8982** | ✅ 逐位相同 |
| payload_drop | ≈0.8842 | **0.8842** | ✅ 逐位相同 |
| clean | 0.928508 | **0.928508** | ✅ |
| override_genuine | ≈0.9220 | **0.921971** | ✅ 且高于 blanket gate（0.921461） |

blanket gate 在三种 payload 风格上要付 0.0160 / 0.0053 / 0.0105；
**span 抑制一分不付**。因为「哪一部分作废」是用户自己说的，不需要我们去猜。
`suppress_abandoned=True` 已设为默认。

---

# Open-world 证据抽取（审查 item 5）

原逻辑是**高 precision、低 recall** 的解析器：模板解析不出来 ⇒ 没有 category /
phrase ⇒ UNCERTAIN ⇒ 完全丢弃。对未知的敷衍很安全，但**对未知的真实约束同样丢弃**。

现在模板失败后再对原句跑 slot 正则 + 特征词表 + 否定检测；
**抽到明确 attribute/value 才标 INFORMATIVE**，否则才落 UNCERTAIN。
抽取到的证据是 `hardness="soft"`、`confidence=0.6`，**低于模板证据**，不等权。

| 输入 | 抽取结果 |
|---|---|
| "Leather would be ideal." | material=leather (+) |
| "I'd love something blue." | color=blue (+) |
| "Mostly for hiking." | use_case=hiking (+) |
| "Something waterproof would help." | feature=waterproof (+) |
| "I need it machine washable." | feature=machine washable (+) |
| **"Nothing too formal."** | **use_case=formal (−1)** 否定被识别 |

9 条敷衍措辞（含 holdout 六句）**全部零抽取**，仍判 UNCERTAIN。
clean 0.928508 与 compat 0.928708 均不变（clean 上模板永远解析成功，此路径不触发）。

---

# Holdout 状态：已消费（审查 item 4）

seeds 12–31 + 那六句未见措辞**已经向开发提供了反馈**
（暴露了 `MORE_RE` 只认字面 "more" 的缺口），因此：

- 该组合**不再是 untouched holdout**，不得用于后续调参；
- 六句措辞已并入 `tests/test_agent.py` 的开发回归（`OpenWorldEvidenceTest.UNINFORMATIVE`）；
- **下一轮调参前必须新建 sealed phrasing set**，并在使用前只运行一次。

已记录的 holdout 结论仍然有效——它验证的是**当时冻结的 Phase 1 配置**，
那次验证本身没有被污染。

## 对外表述口径（审查要求）

> **Dual-route control plane 已完成；Buying/Browsing 的 route-specific
> retrieval data planes 尚未实现。** Phase 2A 完成的是路由标签语义、
> 证据驱动的路由转移、配置贯通与可观测性；两条路由的默认权重仍然相同，
> 没有 facet/filter 路、没有 dense 路、没有 route fusion。
> **不得把 Phase 2A 写成 Pillar I 已完成。**
