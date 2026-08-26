# 07 — 四支柱差距核查 + 泛化加固结果 + 三天路线

## A. 本轮成果：槽位级优雅降级（per-slot graceful degradation）

**动机**：三路审查判定的最大真实风险——若私有模拟器改写约束字符串本身
（payload rewording），精确匹配特征（w_phrase/w_exact/w_field，权重合计 8.5）
全灭，诚实预期跌至 0.67–0.86。

**验证方法**（用户提议的 synthesize 新数据）：构造 payload 改写压力集 v2，
三种机械变换直接改写约束串——`payload_soft`（"Material:alloy"→"made of alloy"）、
`payload_shuffle`（token 逆序去标点）、`payload_drop`（只留最长 2 个 token）。
变换针对 payload，我们的 cue 正则从未见过它们（chrome 模板保持原样，实验隔离）。

**机制**：重排前对每条已披露约束做全候选池探测——凡在池中**有**字面命中的槽位
继续走精确特征；**零命中的「死槽位」**回退到 IDF 加权 token 重叠软匹配
（IDF < 1.5 的高频词不参与，避免 "made" 之类制造噪声）。
在逐字模拟器上死集为空 ⇒ 排序不变 ⇒ **保险费为零**（实测还 +0.0007，
因为它顺带修复了我们自拼接复合短语的匹配缺陷）。

**结果**：

| 套件 | 无保护 | slot_soft=4（新默认） |
|---|---|---|
| 干净模拟器（官方 harness） | 0.9280 | **0.9287** |
| payload_soft | 0.8554 | **0.8783** |
| payload_shuffle | 0.8736 | **0.8984** |
| payload_drop | 0.8536 | **0.8760** |
| chrome casual / terse / verbose | 0.9176 / 0.9233 / 0.8564 | 0.9173 / 0.9240 / 0.8568（不变） |

途中弃置的两个方案（记录为消融）：静态 w_soft（干净集 -0.003 保险费）、
会话级自适应门控（被偶然字面命中翻转，恢复力弱于槽位级）。
权重不敏感（2 与 4 几乎同分）→ 不是过拟合出来的点。

**最坏情形下限更新**：payload 全改写 ≈ 0.876–0.898（此前 0.854–0.874）；
叠加 chrome 未见风格的诚实区间 ≈ **0.85–0.92**（此前 0.67–0.86）。
ask="other" 灾难分支已由 dry_others fallback 兜底。

## B. 四支柱逐条核查（题面 4.2 vs 现状）

| 支柱要求 | 现状 | 差距与对策 |
|---|---|---|
| I. Dual-Track Routing | ✅ 模板路由 buying/browsing/override，零成本 | 已达标；route_overrides 是配套证据 |
| I. Multi-Route Retrieval → LLM Semantic Ranking（keyword+category+vector） | ⚠️ keyword ✅ category（w_cat）✅ vector ✖ LLM ✖ | 软匹配=稀疏向量余弦（诚实表述）；报告需正面论证：recall@200=1.0 ⇒ 加稠密路无召回可救，LLM 重排被断网条款排除，替代 = 特征重排 + 槽位级软匹配。**这是我们与题面字面预期最大的偏离，必须写成有据的设计决策而非回避** |
| II. Information Accumulation | ✅ 增量槽位 | 达标 |
| II. Intent Override "slot erasure and rewriting" | ⚠️ 我们实测 keep 优于 erase（0.87 vs 0.47）并如实解释 | 补一个 **selective erasure** 模式（只擦除与新约束同类冲突的旧槽），作为题面语义的诚实实现 + 消融对比 → 明日实验 |
| II. Proactive Guidance / Over-Generality cutoff | ✖ 未实现 | 评测协议下截断推荐纯亏分 → 实现「检测 + 提问引导」而非「截断」：候选池过载时用池感知的 message/ask 选择；与信息增益提问同一实验 → 明日 |
| III. Personalized Context Distillation | ⚠️ profile 信号弱（已测：常量+弱标签） | preference_tags 软加权补测一次，预期空结果也写进报告（诚实的负结果） |
| III. Adaptive Orchestration | ✅ 两个真实实现：dry_others 提问策略降级、死槽位软匹配降级 | 报告用支柱 III 语言呈现 |
| in-scope 点名的 slot decay | ✖ | 时间衰减词权重 → 明日实验（override 场景可能受益） |
| IV. 指标 | ✅ 全对齐 | — |

## C. 剩余三天

**D1（明日，实验日）**：信息增益提问策略 + 证明其在本模拟器退化为 "other"
（官方点名的 question-value estimation；含 Over-Generality 池感知引导）；
selective erasure 消融；slot decay 消融；preference_tags 补测。
每项都进 experiments.jsonl，正负结果都要。

**D2（提交包日）**：英文报告（含三次审查采纳的弱化措辞、全部消融表、
四支柱映射、披露与声明）；submission/ 目录（agent.py + requirements.txt +
README：Python≥3.10 + FTS5 依赖 + TJ_CONFIG 文档 + 一条命令复现 0.9287）；
公开仓库整理 + 窗口前诚实 commit。

**D3（窗口内）**：实质性 in-window 更新（MRR 冲刺 rank2→1、verbose 残差）、
演示会话 transcript、录视频、Devpost 描述、提交。

8/28（四）下午 4 点 workshop 必问清单：final scoring 是否断网；私有集消息是否
改写、约束是否保持原文；"other" 语义在私有模拟器中的实现。
