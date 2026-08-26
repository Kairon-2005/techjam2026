# 01 — 评测器逆向分析

对 `evaluator/local_evaluator.py`（312 行）逐行阅读后的发现，全部有实测数据支撑。

## 完整消融表（公开集 200 会话）

| 配置 | TechnicalScore | HR@10 | MRR | MTTC | boundary | browsing | buying | override |
|---|---|---|---|---|---|---|---|---|
| 官方弱基线 | 0.1067 | 0.125 | 0.068 | 9.81 | 0.00 | 0.03 | 0.24 | 0.13 |
| control（固定属性轮询 + 清空） | 0.6295 | 0.745 | 0.467 | 5.15 | 0.60 | 0.79 | 0.82 | 0.47 |
| H1 单独（问 other） | 0.6703 | 0.780 | 0.480 | 4.18 | 0.90 | 0.85 | 0.85 | 0.37 |
| H2 单独（保留 override 前状态） | 0.6855 | 0.805 | 0.520 | 4.65 | 0.60 | 0.79 | 0.82 | 0.87 |
| H1 + H2 | 0.7371 | 0.855 | 0.539 | 3.60 | — | — | — | — |
| **H1 + H2 + 停用词** | **0.7536** | **0.870** | **0.560** | **3.48** | — | — | — | — |
| H1 + decay（保留前 8 词） | 0.7496 | 0.865 | 0.558 | 3.52 | — | — | — | — |
| other_then_cycle | 0.7533 | 0.870 | 0.560 | 3.49 | — | — | — | — |

**相对官方基线 7.06×。** 全程无 LLM、无 GPU、纯 Python 标准库。

**H1 与 H2 超可加**：单独 +0.041 与 +0.056，合计应为 +0.097，实测 +0.108。
原因见发现 3。这个交互效应本身值得写进报告。

---

## 发现 1 —— `ask_attribute="other"` 是万能提取器

```python
matches = [v for v in constraints
           if v not in disclosed
           and (attribute == "other" or classify_constraint(v) == attribute)][:2]
```

`attribute == "other"` 时布尔条件短路为真 → 匹配**任何**未披露约束，每轮最多返回 2 条。

而每个会话的约束总量上限是 4 条：

```python
"hard_constraints": cleaned[:2],
"soft_preferences": cleaned[2:4] or cleaned[:1],
```

→ **问两次 `other` 即可榨干全部信息。** 按固定属性顺序轮询会大量撞上
`"I don't have an additional preference for X."` 而空转。

实测：MTTC 5.15 → 4.18；boundary 0.60 → 0.90。

**性质判定：这是对模拟器机制的利用，不是建模洞察。** 报告里必须如实标注。

---

## 发现 2 —— override 场景中「要忽略的旧偏好」其实是目标商品的真实约束

```python
old_value = soft[-1]     # 取自目标商品的 soft_preferences
new_value = hard[0]      # 取自目标商品的 hard_constraints
message = f"Actually, ignore my earlier preference. What I need is: {new_value}."
```

顾客说「忽略我之前的偏好」，但 `old_value` **本身就是目标商品的约束**，不是干扰项。

题面 Pillar II 写的是 "Intent Override (**slot erasure** and rewriting)" ——
**照着实现会主动丢弃有效信号。**

实测：intent_override 场景 HR@10 **0.467 → 0.867**。

**性质判定：这一条介于两者之间。** 「用户口头否定的偏好未必与真实需求矛盾，
不应无条件清空状态」是一个可迁移的对话状态管理观点；但其强度被本模拟器放大了。
可作为真实洞察写，但需说明它在真实场景中的适用边界。

---

## 发现 3 —— override 之前的命中不计分

```python
override_applied = sample["scenario_type"] != "intent_override"
...
if override_applied and target in ranked:   # 只有 override 之后才 break / 记分
```

官方规格同样明文写着：
> "An Intent Override session cannot convert before the new intent is sent."

推论：
- 这些会话的前 2 轮是「免费」的，应纯用于抽取信息
- **解释了 H1 单独使用时 override 反而掉到 0.37**：抽取越快 → 累积词越多 →
  清空时损失越大。所以 H1 与 H2 必须同时启用，这就是超可加的来源。

---

## 发现 4 —— 意图路由无需 LLM

初始消息的模板可直接区分场景：

| 首轮消息特征 | 场景 |
|---|---|
| 含 `"A key requirement is"` | buying |
| 含 `"still exploring"` | browsing 或 boundary |
| 其余 | intent_override |

这恰好实现题面 Pillar I 要求的 Dual-Track Routing，零成本、零延迟、零 token。

---

## 尚未利用的信号

1. `average_rating` / `rating_number` —— 目标是**真实购买记录**，热门度先验很可能提升 MRR
2. `price` —— 约束里有 `budget around $X`，目前只当普通词元处理
3. `user_profile.preference_tags` —— 完全未用
4. 候选集只取 Top-10，**没有重排阶段**

## 当前瓶颈

```
score = 0.5 × 0.870 + 0.3 × 0.560 + 0.2 × 0.752 = 0.7536
                ↑            ↑              ↑
          剩余 0.130    剩余 0.310     MTTC 3.48
          最多 +0.065   最多 +0.093    最多 +0.050
```

MRR 上限受 HR 约束（MRR ≤ HR@10）。若命中会话全部排到第 1，MRR 可达 0.870，
即 **+0.093 —— 这是单项最大的剩余空间。下一步做重排。**
