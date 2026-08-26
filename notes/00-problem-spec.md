# 00 — Track 4 题目规格精读

来源：Early Bird 题面 §4 + 参赛包 `docs/competition_specification.md`、`docs/submission_rules.md`、
`docs/evaluation_config.json`。以参赛包为准（更精确、且是官方冻结产物）。

## 任务

多轮购物 agent：在**最多 10 轮**内，把一个隐藏的目标商品**尽早、且排名尽可能靠前**地放进 Top-10。

目标来自 Amazon Reviews 2023 的真实购买记录。顾客消息由一张隐藏的 intent card
（从商品元数据派生）模拟生成——**数据集里没有真实的购物对话**。

## 评分

```
HitRate@10 = 命中会话数 / N
MRR        = mean(1 / target_rank)，未命中记 0
MTTC       = mean(首次命中轮次)，未命中记 11
Efficiency = clip((11 - MTTC) / 10, 0, 1)

TechnicalScore = 0.50 × HitRate@10 + 0.30 × MRR + 0.20 × Efficiency
```

官方弱基线：HR@10 `0.125` / MRR `0.068034` / MTTC `9.81` / **TechnicalScore `0.10671`**

评委 rubric（占比）：Technical Execution 35 / Innovation & Problem Insight 20 /
Impact & Relevance 20 / Feasibility & Practicality 15 / Presentation 10（仅决赛）。
→ **TechnicalScore 不等于那 35%。** 它是自证材料，工程质量与叙事同样计入。

## 场景分布（两个 split 完全一致）

| 场景 | 占比 | 公开集 n | 私有集 n | 特点 |
|---|---|---|---|---|
| Buying | 40% | 80 | 320 | 首轮即披露一条 hard constraint |
| Browsing | 40% | 80 | 320 | 首轮很模糊，只有品类 |
| Intent Override | 15% | 30 | 120 | 第 3 或第 4 轮替换先前偏好 |
| Boundary | 5% | 10 | 40 | 顾客可能对某属性无偏好 |

**公开集与私有集的场景配比相同 → 公开集上的结论可以外推。**

## 硬约束

- **10 轮硬上限**，超出直接零分
- 商品目录**只读**，禁止结构性修改或注入假 ASIN
- 禁止训练 / 全参微调基座 LLM
- 禁止部署重型工业向量库，**必须全内存轻量运行**
- 仅文本：文本目录、结构化元数据、文本对话
- UI/UX 不计分（纯后端 API + headless pipeline 评测）

## ⚠️ 最关键的一条：final scoring 可能断网

> "For official final scoring, organizer policy may disable network access."
> "The organizer reserves the right to run your submission under CPU, memory,
> timeout, and network restrictions."

**推论：任何依赖外部 LLM API 的方案都可能在最终评分时直接失效。**
本项目的设计原则因此确定为：

1. **主路径必须完全本地、零网络、纯 CPU 可跑**
2. 若引入 LLM，只能作为**可选增强**，且必须有等价的离线 fallback
3. 提交时必须明确声明是否需要网络（规则要求）

这一条同时把「别人有钱买 API 额度」的优势也抹平了。

## 可见字段

`parent_asin`、`title`、`features`、`description`、`price`、`categories`、
`details`、`average_rating`、`rating_number`、`store`。仅 `parent_asin` 计分。

> 注意：`average_rating` / `rating_number` 目前**完全没用上**。目标是真实购买记录，
> 热门度先验很可能对 MRR 有效。

`user_profile` 是脱敏聚合：购买频次、评分风格、`preference_tags`。**目前也完全没用上。**

## 接口契约

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None: ...
    def respond(self, session_id: str, user_message: str,
                turn: int, top_k: int) -> dict:
        return {"message": str,              # 面向顾客的自然语言
                "ask_attribute": str | None,  # 模拟器只认这个字段，不解析 message
                "recommendations": [{"parent_asin": "B000..."}],
                "usage": {"prompt_tokens": int, "completion_tokens": int}}
```

- `ask_attribute` ∈ {category, material, color, size, style, brand, budget,
  feature, use_case, other} 或 `null`
- 无效 / 重复 ID 会被剔除，只有**前 10 个有效唯一 ID**计分 → 返回垃圾会白白占掉名额
- 可选的数值 `score` 字段被接受但**忽略**
- **异常、非法输出、超时都可能直接判为 miss** → 健壮性是硬需求

## 官方点名的创新方向（= 评委想看到的）

- Buying / Browsing 路由与多路检索
- 混合检索与语义重排
- 结构化约束状态、intent override 处理、动态上下文构造
- **自适应澄清与「提问价值估计」** ← 官方明确点名
- 使用聚合 profile 的安全个性化
- 失败检测、策略切换、低延迟、低 token 成本
- 可解释的推荐理由

## 交付物

- 源码 + 安装与复现说明（一条命令跑通官方 harness）
- 符合接口的 Agent（单一入口文件导出 `Agent`）
- 简短报告：架构、模型选择、成本、局限、分工
- 一次完整的多轮会话演示
- 延迟 / token 用量 / 估算成本的披露
- Devpost 项目描述 + 公开 GitHub 仓库 + YouTube 演示视频
