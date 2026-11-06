# 参考代码与复用登记

## 登记规则

本文件记录真正引入的第三方实现及修改。2026-10-25 已完成首批画像流程适配，见下方对应关系；只有提示词是原文摘取，编排和结构化校验为本项目改写。自有适配流程已做小批量API实验，但未完整复现原论文。

## Embracing Imperfection

- 上游：<https://github.com/McCartney01/student_sim>
- 本地参考：`../../paper-repos/student_sim/`
- 本次核查的上游提交：`8e670a4c3e58833b3435440d961946d6a13aa687`。
- 本地根目录 LICENSE 为 MIT，声明 Copyright (c) 2024 Gustavo Ye；实际引入文件前仍需核对文件内声明及第三方依赖，保留适用声明，不将其直接改为本项目版权。
- 验证包括源码核对、离线测试及自有适配流程的API抽样，不是论文复现。
- 借鉴范围：`src/student_profile.py` 中历史输入、知识点汇总与检索流程；`src/prompt.py` 中相关提示词；按需评估 `src/nano_graphrag/` 依赖。
- 自有适配已实现统一ID、按学生隔离证据、增量更新和受控调用；旧推断修正仍暂缓。
- 不引入当前主流程：`main.py` 的多模型批量实验，`src/sbs.py` 的行为预测与答案模拟。
- 原逻辑直接消费 `desc` 和 `error_desc`，不能误写成已自动完成可靠的题目分析与判题。
- 原1—5分汇总属于模型评价，不能作为校准答对概率。

### 已引入与修改

| 上游部分 | 本项目位置 | 处理方式 |
|---|---|---|
| `src/prompt.py` 的 get_library、summarize_kc、retrieve | `profiling/prompts.py` | 三段原文保留；当前仅使用 summarize_kc 的目标部分，get_library 和 retrieve 模板未启用 |
| `StudentProfile.insert` | `profiling/student_profile.py` 的 insert | 自行组装题目与学生代码，不传入 desc/error_desc；逐条提取，替代拼接后调用 GraphRAG；补用户、划分、来源校验 |
| `generate_desc_for_every_kc` | 同名方法 | 保留按知识点汇总 Good/Bad 的流程及原提示词目标部分；使用结构化状态计数，新增 Unknown，移除1—5分输出 |
| `retrieve` | 同名方法 | 改写为知识点 ID 精确检索画像与证据，不是迁入原图检索算法 |
| 根目录 LICENSE | `profiling/LICENSE` | 原文保留 MIT 及版权声明 |

未引入 GraphRAG 引擎、向量嵌入、上游模型封装、50线程批处理、学生模拟与缓存机制。模型由 `complete(stage, prompt) -> dict` 注入，不绑定服务商；真实提取已抽样，仍存在误归因；离线合成返回只验证程序行为，不代表真实学习表现。

## 其他参考

CBEGRec借鉴概念组合和目标选择结构。2026-10-27再次静态核对本地 `../../paper-repos/CBEGRec/src/concept_bundling/goal_oriented_concept_bundling.py` 的identify_frontier、mine_preliminary_bundles及评分职责；本地为快照，没有可读Git提交元数据，该文件SHA256为438bea0e62977efe5e03a8982c89d6b7f64796791450c68bf856c2d3e2f42604。没有迁入其源码、调用原实验入口、加载KT或完整复现。

本项目的recommendation/context.py和prepare_recommendation.py为自行编写的接口，不采用概率前沿或熵评分，也没有用Bad排序替代原算法。已实现A候选再选择与B直接推荐对照，正式使用B；recommendation/comparison.py为本项目自行实现，不迁入CBEGRec算法源码。模型方案不称为原论文复现。普通显式选择仍不称为CBEGRec推荐结果。

EduAgentQG仅借鉴目标驱动的候选生成、检查与修订，尚未引入其源码。涉及具体代码时仍需核对许可、依赖和适配成本。
