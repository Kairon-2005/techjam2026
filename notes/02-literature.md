# 02 — 开源最优解法调研

每条都标注**是否适用于本题约束**（10 轮上限、纯内存、禁微调基座、**final scoring 可能断网**）。

## A. 检索层

### A1. 稀疏 / 词法检索
当前用 SQLite FTS5 + BM25，列权重 `(0, 6, 4, 2.5, 2.5, 1.5, 1)`。
- **BM25 参数**：FTS5 的 `bm25()` 固定 k1=1.2、b=0.75，不可调。若要调参需自己实现，
  或改用 `rank_bm25`（纯 Python，50k 文档可接受）。**可做消融，成本低。**
- **查询扩展（RM3 / 伪相关反馈）**：用首轮 Top-K 结果的高频词扩展查询。
  经典且无需模型。⚠️ 但本题的「新信息」来自顾客回复而非语料，收益可能有限，值得一试。
- **文档扩展（doc2query）**：需要生成模型跑全目录，离线可做但成本高。**暂不考虑。**

### A2. 稠密检索
- 候选模型：`all-MiniLM-L6-v2`（22M，384 维）、`bge-small-en-v1.5`（33M，384 维）、
  `gte-small`。全部远小于任何参数上限，CPU 可跑。
- 50k 商品 × 384 维 float16 ≈ **38 MB**；PCA 降到 128 维 ≈ 12 MB。
- ⚠️ **断网风险**：模型权重必须随仓库提供或在 setup 阶段下载。规则允许
  「lightweight local assets」，但 90MB 的模型算不算 lightweight 有解释空间。
  **更稳的做法：离线预计算 embedding 矩阵并只提交 `.npy`**，推理时不加载模型
  —— 但这样就无法编码查询。折中：预计算商品向量 + 提交一个极小的编码器
  （MiniLM 量化后约 20MB），或退而用纯词法方案。**需实测收益后再决定是否值得这个风险。**
- ⚠️ **规则**："infrastructure-heavy vector databases" 出局 → 用 numpy 矩阵乘法，
  50k × 384 的暴力检索约 20 ms，**完全够用，不需要 faiss**。

### A3. 混合融合
- **RRF（Reciprocal Rank Fusion）**：`score(d) = Σ 1/(k + rank_i(d))`，业界默认 k=60。
  无需调权重、跨系统可比，是稳健的默认选择。
- **加权分数融合**：需要归一化，效果可能更好但更脆。
- 判定：**先上 RRF（k=60），再试加权。** 用户在 SenseTime 已实践过 RRF。

## B. 重排层 —— 当前最大空间（MRR 剩余 +0.093）

### B1. Cross-encoder
`bge-reranker-v2-m3`、`ms-marco-MiniLM-L-6-v2` 是标准选择。
⚠️ **不适用**：200 会话 × 约 3.5 轮 × 300 候选 ≈ 21 万次 cross-encoder 前向，
CPU 上不可接受，且违背「低延迟」导向。**排除。**

### B2. 轻量特征重排（推荐）
FTS 取 N 个候选（N=200~500），再用廉价特征在 Python 里重打分：

| 特征 | 依据 |
|---|---|
| BM25 原始分 | 词法相关性 |
| 约束**字符串级**精确/子串匹配数 | 约束原文逐字来自目标商品的 features/details |
| `average_rating` × log(`rating_number`) | 目标是真实购买记录 → 热门度先验 |
| price 与 `budget around $X` 的接近度 | budget 约束目前被当普通词处理 |
| 类目匹配（首轮 `coarse_category` 来自目标商品类目） | 强信号 |
| 标题长度 / 字段覆盖度 | 归一化项 |

线性加权，权重用公开集上的交叉验证拟合。⚠️ 只有 200 会话，**必须做 k-fold 防过拟合**，
并在报告中说明。**这是下一步要做的事。**

### B3. LLM 重排
题面 Pillar I 明确要求 "LLM Semantic Ranking"。
⚠️ 与断网风险直接冲突。**结论：作为可选增强实现，默认关闭，并提供 B2 作为离线 fallback，
在报告中明确声明。** 这本身就是规则鼓励的做法（"if your system has an offline fallback, describe it"）。

## C. 提问策略层 —— 简历价值最高的一层

### C1. 文献标准做法
会话式推荐（CRS）的经典框架把每轮决策拆成「**问属性 还是 推荐商品**」，
再决定「**问哪个属性**」。

| 系统 | 提问策略 |
|---|---|
| Abs Greedy | 只推荐，从不提问（下界基线） |
| Max Entropy | 规则式：选候选集中熵最大的属性 |
| CRM (2018) | RL + belief tracker |
| EAR (WSDM'20) | Estimation–Action–Reflection 三阶段 |
| CPR / SCPR (KDD'20) | 图上路径推理；**加权信息熵** `g(u,p,V) = −prob(p)·log₂prob(p)`；RL 动作空间压缩到 2（ask / rec） |
| UNICORN (SIGIR'21) | 图 RL 统一策略 |

近期（arXiv 2603.11399）给出更直接可用的形式：
```
H(d)      = −Σ_v p(v) log₂ p(v)          # 候选集中属性 d 的取值分布熵
H_norm(d) = H(d) / log₂|Val(d)|          # 按取值基数归一化到 [0,1]
选择 argmax H_norm(d)，若全部 < τ（论文取 0.3）则转为推荐
```

### C2. 对本题的判定 —— 一个必须诚实处理的问题

**在本模拟器下，信息增益最优解会退化成常数策略「永远问 other」**，因为
`customer_reply` 对 `other` 无条件匹配任意未披露约束（发现 1）。
任何 IG 策略都不可能超过它。

因此**不能**声称「我们用信息增益策略拿到了 7 倍提升」——那是不诚实的。

**正确做法（也是更好的故事）**：
1. 实现通用的、基于候选集熵的属性选择策略
2. 用实验**证明**在本模拟器的披露模型下最优动作退化为 `other`，并给出退化的原因
3. 量化拆分：多少增益来自策略设计，多少来自模拟器结构
4. 讨论：若模拟器改为按属性严格匹配（更接近真实用户），IG 策略会带来多少收益
   —— 可以自己改一个 `strict` 模式的模拟器来做这个反事实实验

这一层才是面试和申研时经得起追问的部分，也正好命中官方点名的
**"adaptive clarification and question-value estimation"**。

## D. 已确定的设计原则

1. **主路径全本地、零网络、纯 CPU** —— 由 `submission_rules.md` 的断网条款倒推
2. 任何 LLM 组件都必须可关闭且有等价离线 fallback
3. 不引入向量数据库，numpy 暴力检索足够
4. 一切改动必须经过 `lab/sweep.py` 量化，进 `experiments.jsonl`
5. 报告中如实区分「模拟器机制利用」与「可迁移的建模洞察」

## 参考

- Advances and challenges in conversational recommender systems: A survey — https://www.sciencedirect.com/science/article/pii/S2666651021000164
- Interactive Path Reasoning on Graph for Conversational Recommendation (CPR/SCPR, KDD'20) — http://staff.ustc.edu.cn/~hexn/papers/kdd20-graph-crs.pdf
- Entropy Guided Diversification and Preference Elicitation in Agentic Recommendation Systems — https://arxiv.org/html/2603.11399
- CRSPapers（会话推荐论文列表） — https://github.com/Zilize/CRSPapers
- Hybrid Search for RAG: BM25 + Dense (2026) — https://denser.ai/blog/hybrid-search-for-rag/
