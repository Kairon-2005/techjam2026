# TechJam 2026 · Track 4 — Shopping Copilot

多轮会话式商品检索 agent。10 轮预算内把隐藏目标商品排进 Top-10，越早越靠前越好。

## 当前状态

| | TechnicalScore | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| 官方弱基线 | 0.1067 | 0.125 | 0.068 | 9.81 |
| 阶段一：对话策略 | 0.7536 | 0.870 | 0.560 | 3.48 |
| **阶段二：重排层（当前）** | **0.9280** | **0.995** | **0.837** | **2.03** |
| *现实联合上界* | *0.9822* | *1.000* | *1.000* | *1.89* |

**相对基线 8.70×。** 纯 Python 标准库，零网络、零 GPU、零模型权重，token 用量 0，
单次全量评测约 30 秒。默认配置即最优配置——`python3 -m evaluator.local_evaluator`
不传任何参数即可复现 0.9280。

## 目录结构

```
notes/          精读与调研笔记（决策依据）
  00-problem-spec.md         题目 / 约束 / 资源 / 评分标准
  01-evaluator-mechanics.md  评测器逆向分析 + 完整消融表
  02-literature.md           开源最优解法调研与适用性判定
NOTES.md        决策日志（做了什么决定、依据、可推翻它的证据）
lab/            实验基建
  sweep.py          跑多组配置，目录索引跨配置复用
  analyze.py        从 experiments.jsonl 生成消融表
  experiments.jsonl 追加式实验日志（配置 / 指标 / 分场景 / 耗时 / git hash / 时间戳）
starter/agent.py    提交用 agent，所有行为都是配置项
docs/           主办方原始文档（勿改）
data/           冻结目录 + 200 条公开会话
evaluator/      官方评测器（勿改）
```

## 用法

```bash
python3 -m lab.sweep                      # 跑默认消融组
python3 -m lab.sweep name='{"ask_policy":"other"}'   # 跑单个自定义配置
python3 -m lab.analyze                    # 打印消融表
python3 -m evaluator.local_evaluator      # 官方 harness，用默认配置
```

## 设计原则

1. **主路径必须零网络、纯 CPU**——`docs/submission_rules.md` 明文规定 final scoring
   可能禁用网络访问。任何 LLM 组件都必须可关闭且有等价的离线 fallback。
2. 不引入向量数据库。50k × 384 的 numpy 暴力检索约 20 ms，足够。
3. 一切改动必须经过 `lab/sweep.py` 量化并写入 `experiments.jsonl`，不接受「感觉更好」。
4. 报告中如实区分「模拟器机制利用」与「可迁移的建模洞察」。

## 架构

```
用户消息 → 意图路由(buying/browsing/override，零成本模板匹配)
        → 结构化约束状态(parse_message：品类 + 约束短语列表，override 不清空)
        → 提问策略(ask_attribute)
        → FTS5/BM25 召回 top-100
        → 特征线性重排(phrase / exact / field / idf / cat / popularity / bm25)
        → top-10
```

## 外部审查回应（notes/08）

10 条批评中 4 条可实证，全部实现成默认关闭的配置项后量化：**2 条采纳、2 条否决**。

| 批评 | 判定 | 证据 |
|---|---|---|
| 双轨路由无效 | 描述属实，**药方否决** | `w_pop=6` 的 +0.0013 = 2 好 / 7 坏，5 折仅 2 折改善 |
| 覆盖不是真擦除 | 描述属实，**药方否决** | 真实矛盾覆盖下 keep 0.9233 > slot 0.9140 > erase 0.8458 |
| 提问是模拟器捷径 | 属实，**采纳** | `other_then_pool` 17% 轮次真实信息增益提问，代价 −0.0002 |
| 资源占用未披露 | 属实，**采纳** | 惰性索引：7.70s/430MB → 5.07s/393MB，分数不变 |

否决两条的共同原因：**本方案只打分不过滤**，过时证据无法排除正确商品，
因此「遗忘」与「按路由分叉」的收益都低于其代价。

## 待办

- [x] 重排层 —— 0.7536 → 0.9280
- [x] 热门度先验 —— 单个最强特征，+0.114
- [x] 交叉验证调参 —— 5 折，均值 0.9280 ± 0.0203
- [x] 验证 `price` 只有 21% 商品可用 → 收益有限，已降优先级
- [x] 验证 `user_profile` 个性化信号极弱 → 已放弃
- [ ] MRR：把 rank 2–5 提到 rank 1（剩余空间 +0.053，占全部剩余的 91%）
- [ ] 通用信息增益提问策略 + 证明其在本模拟器下退化为 `other`（简历/报告价值）
- [ ] 提交前：README、requirements、一条命令复现、报告、演示视频
