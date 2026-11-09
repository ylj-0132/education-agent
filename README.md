# 教育智能体

本科毕设原型：历史证据 → 知识点画像 → 练习目标推荐 → 出题与检查 → 作答 → 更新。首轮使用Java_5；教师直接指定知识点也可出题。

## 首次启动

在仓库根目录运行（当前开发环境为 Python 3.12）：

```powershell
python serve_workbench.py
```

打开 <http://127.0.0.1:8765>。服务使用 Python 标准库，无需安装前端依赖；启动和浏览页面不需要 API 密钥，不会调用模型或运行 Java。按 `Ctrl+C` 停止服务。

**新克隆的仓库不带学生数据和实验结果，页面为空是正常情况。** “已有结果演示”依赖本机已保存的结果，不会自动下载数据或重新生成。付费任务需另行在本地复制 `.env.example` 为 `.env` 并填写配置，再按具体入口核对输入与调用额度。

## 目录导航

| 位置 | 用途 |
| --- | --- |
| 根目录正式入口 | 数据导入、画像、推荐、出题、接收作答及 Java 验证；见下文入口表 |
| `profiling/` | 学习证据、画像、增量更新、模型调用与检查点 |
| `recommendation/` | 推荐上下文、B 推荐、题目使用历史及 A/B 对照 |
| `generation/`、`submissions/` | 出题审核、Java 验证与版本绑定作答接收 |
| `webapp/` | 本地 HTTP 服务与原生网页界面 |
| `config/` | 公共知识目录设计初稿 |
| `tests/` | 工程测试；不代表教学效果验证 |
| `docs/` | 方法说明、可靠运行、来源许可及开发记录 |

`compare_recommendation.py`、`sweep_recommendation.py` 是推荐对照实验入口。`continue_java5_three.py`、`diagnose_api.py` 是绑定特定历史数据、检查点或异常的专用脚本，保留用于追溯，不是首次启动步骤，也不应直接用于新批次。为保留历史恢复关联，暂不移动或批量改写这些脚本。

`runs/`、`artifacts/`、`cache/` 和数据目录仅留在本机，不提交 Git。RSI 四方案试验位于工作目录中的独立实验区，目前未接入正式系统，也不包含在本仓库中。

### 后端代码职责

核心流程放在 `profiling/`、`recommendation/`、`generation/` 和 `submissions/`；根目录 CLI 负责参数与任务入口。本地 HTTP 服务调用这些 CLI，保持相同的校验、预算和检查点机制。

| 本地服务文件 | 负责内容 |
| --- | --- |
| `webapp/server.py` | HTTP 路由、请求校验、响应与静态资源 |
| `webapp/service.py` | 统一应用入口，连接下面三个模块 |
| `webapp/artifacts.py` | 已有结果索引、学生归属关联、白名单读取、作答草稿保存 |
| `webapp/commands.py` | 操作与字段定义、请求转 CLI 参数；只构造命令，不执行 |
| `webapp/jobs.py` | 串行任务启动、进程输出收集、日志脱敏和会话状态 |

结果读取不启动任务，草稿保存不等于接收作答。任务状态仅在当前服务会话内保存，持久恢复仍由 CLI 的 SQLite 检查点负责。`webapp/static/` 是现有展示页面，与核心学习流程分开。

## 当前状态

核心命令行链路已实现：画像及增量更新、正式B推荐、出题与六维审核、轻量Java验证、作答接收、题目使用记录、trace与检查点恢复。最后完整离线验证为142项通过（2026-10-30）。真实 API 与小批量 Java 实验另存于本地《项目实验记录》，不随代码仓库分发；上述测试数量是历史记录，不代表最新全套测试已重跑。

目前是小规模、人工复核的原型：没有真实学生使用新题的学习效果证据；模型仍可能误归因；生成题保持`needs_review`及`ready_for_student=false`，不自动交付。新题接收接口已验证，历史回放不等于新题真实反馈。

展示按《目前进展》的讲解顺序组织为“系统方法 → 实际案例 → 技能进化实验”。技能实验页（`/#skills`）为截至 2026-11-05 的固定结果摘要，数值核对自独立实验区 `final-results.json`，同时显示严格字面与允许输入缓冲两种统计口径；不表示技能机制已经接入正式系统。

## 数据与配置

- Java_5：5名用户、200条train、50条test；保留原划分、来源和哈希，不将test用于画像。没有可靠时间、独立判题标签或统一知识标签。
- `config/java-basics.json`：31个候选知识点，11条条件先修、23条关联；为有来源的设计初稿，未获教师完整审核，不是完整课程图谱。
- 外部`desc/error_desc`仅存档追溯，不输入画像模型；实际题干和代码结合独立运行观测分析。画像Good/Bad/Unknown不是概率。
- Python标准库实现；无需安装模型框架。Java验证可指定`C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr`，不需改PATH。

本地`.env`参考`.env.example`；不要公开密钥。

| 环节 | 变量 | 当前模型 |
| --- | --- | --- |
| 作答表现提取 | ZZZ_STRONG_MODEL | gpt-5.6-luna（临时） |
| 六维审核 | ZZZ_STRONG_MODEL | gpt-5.6-luna（临时） |
| 画像汇总、推荐、生成与内容修订 | ZZZ_WEAK_MODEL | gpt-5.6-luna |

`ZZZ_MODEL`兼容旧配置及历史实验脚本。正式任务共享调用预算，逐条记录实际模型；混合画像含stage_models。默认单次输出上限1500、超时60秒，实际预算以入口及.env较小值为准；不是人民币费用上限。

2026-11-04按用户要求临时全环节使用Luna，后续新任务生效，旧画像和实验结果不重算、不改标。恢复此前分工时，将本地`.env`的`ZZZ_STRONG_MODEL`改回`gpt-5.6-terra`，其余正式环节保留Luna；不得换模型后续跑同一旧检查点。本次切换前的模型字段保存在`artifacts/luna-diagnostics-20261104/model-switch.json`，不含密钥。

## 正式入口

### 本地前端工作台

在仓库目录执行：

```powershell
python serve_workbench.py
```

用浏览器打开 `http://127.0.0.1:8765`。仅监听本机，不是公开网站或多用户认证服务。无需安装前端依赖；后端使用 Python 标准库。

侧栏“实际案例”（`http://127.0.0.1:8765/#demo`）固定展示 `java_u344869639` 的原始作答 → 画像 → 推荐 → 生成题 → 六维审核。使用已有全量画像、student1-recommendation 和 student1-generation-fixed，不发起新任务；加载时核对学生、推荐关联及引用证据。原有栏目仍保留全部记录。该入口依赖本机结果文件，Git 克隆不包含学生数据，缺失时会提示不可用。

工作台可查看画像及原始证据、生成题与六维审核、Java 验证结果，并通过现有命令行入口预览和启动画像、推荐、出题、作答接收、增量更新及 Java 验证。先预览相同输入，勾选本次确认后再启动；API 请求会计费，Java 执行另需逐份审阅后的批准哈希。仅打开页面或查看记录不调用模型、不执行 Java。

作答接收页可从生成题创建输入草稿，默认 `test_fixture`；真实学生提交才可声明 `student`。草稿保存在 `artifacts/workbench-inputs/`，保存草稿不等于接收，更不等于更新画像。用例仍由调用方准备独立核验的 JSON 文件。Java 使用 `LOCAL_JAVA_TEST_HOME` 或上文的 PyCharm JBR 路径。

本地服务串行启动任务，新结果进入 `runs/workbench-<任务编号>/`。页面任务列表为当前服务会话状态，重启后仍可读取已保存产物；恢复旧任务仍使用原 CLI 和原输入，页面不自动续跑。页面不提供自动发布、自动运行生成代码、自动创建用例或修改旧推断。不要在任务进行中关闭本地服务。运行结束后点击“刷新数据”读取新增结果。

验证说明：新增服务与任务轮询有离线测试；开发期间未调用付费模型、未执行学生 Java。浏览器自动化宿主不可用，尚未完成实际浏览器视觉及点击验收；HTTP 资源、数据接口和 JS 语法已检查。

```powershell
python -m unittest discover -s tests -p test_workbench.py
node --test tests/test_job_poller.cjs
```

### Java_5全量入库与画像

全量入口为`run_java5_batch.py`，默认仅预览。核对原始文件与既有审计manifest的哈希后，`--import-only`把全部train/test记录写入本批`dataset.sqlite3`；`--execute`只用train生成画像，不运行Java，不推荐或出题。每名学生独立检查点，完成全部训练提取和知识点汇总后才登记画像。原始250条记录与测试集隔离保留，原数据和旧画像不覆盖。

```powershell
python run_java5_batch.py --data-root ../paper-screening/data-audit-20261025/student_sim --output runs/java5-full-20261103
python run_java5_batch.py --data-root ../paper-screening/data-audit-20261025/student_sim --output runs/java5-full-20261103 --import-only
python run_java5_batch.py --data-root ../paper-screening/data-audit-20261025/student_sim --output runs/java5-full-20261103 --execute --max-calls-per-student 142 --batch-max-calls 710
```

第三条是付费执行命令：本次200条训练记录加最多155次知识点汇总，共最多355个逻辑请求；每步最多两次尝试，对应每学生142次、全批710次硬上限。这是2026-11-03用户单独确认的本批额度，不改变旧小样本入口或`.env`的12次上限；不是人民币费用上限，也不代表未来批次自动授权。

恢复需原命令加`--resume`，保留相同输入、实现、配置和额度。任何学生未完整完成即停止后续学生；未知请求不会因恢复重发。`batch-report.json`报告完成情况，子目录保存每学生`profile.json`、`records.json`和SQLite检查点。数据库仅登记完整画像；工作台点击“刷新数据”即可读取这些画像及证据。数据库是本地批次存储，不是多用户服务或共享学生状态。

### 小批量命令行

在本仓库运行，先用`--help`查看参数。模型入口默认预览；加`--execute --output runs/新任务`才调用API。`submit_answer.py`用`--save`保存，Java入口另需批准哈希。

| 命令 | 用途 |
| --- | --- |
| java5_data.py | 只读导入原始数据，输出至不存在的新目录 |
| run_profile.py | 从少量训练作答构建画像 |
| update_profile.py | 追加训练记录或绑定的新题作答，更新相关状态 |
| run_recommendation.py | 正式B，一次模型选择知识点或组合 |
| generate_question.py | 教师目标或正式推荐入口；六维审核、最多一次修订 |
| submit_answer.py | 生成答题模板、核验并接收版本绑定的作答 |
| verify_java.py | 生成题参考代码的人工批准验证 |
| verify_submission.py | 历史或已接收学生代码的人工批准验证 |

画像与推荐示例（均为预览）：

```powershell
python run_profile.py --records artifacts/java5-initial/review-batch.json --student java_u520480375
python run_recommendation.py --student java_u520480375 --scope java.array java.loop --profile runs/java5-key-006/profile.json
python generate_question.py --student java_u520480375 --scope java.array java.loop --targets java.array java.loop --requirement "生成Java入门诊断题"
python generate_question.py --recommendation-run runs/已完成的正式推荐任务
```

006只是单题开发画像，不能当完整学史。正式B区分recommended、insufficient_evidence、failed，只有成功才交接；A/B对照目录不能冒充正式推荐任务。直接出题当前需明确知识点ID，不包含自由文本自动识别目标。

出题输出含题面、参考代码、解析、目标位置、审核及修订版本。`needs_split`需拆题，`rejected`内容未通过，`needs_review`待复核，`failed`为执行失败。命令退出0不代表题目已可交付。

出题、修订和审核提示均禁止原题及只改名词、场景或数值的近重复题，不允许以复习理由豁免。允许继续练习同一知识点或基础算法，但要求任务或关键推理有实质变化；审核模型在六维审核的`student_fit`维度检查重复。对照范围限于实际提供的历史题目，提示约束不等于已经实现全历史语义去重。

出题与六维审核的输出上限默认6000 token（含模型推理），可用`--max-output-tokens`显式调整，不再沿用画像的`ZZZ_MAX_TOKENS`。实际值写入检查点，恢复时必须一致。coverage只列本轮目标，输入、输出等辅助知识不扩大目标集合。B推荐交接的完整证据保存在`request.json`；模型提示保留全部目标相关观察和汇总，但原始题干、代码只展开推荐明确引用的记录，其余保留来源索引。教师直接目标入口仍保留全部相关原始证据。

### 接收与更新

```powershell
python submit_answer.py --generation-run runs/某出题任务
python submit_answer.py --generation-run runs/某出题任务 --submission answer.json --output runs/新接收任务 --save
python update_profile.py --base-profile runs/基线任务/profile.json --submission-run runs/新接收任务 --output runs/新画像任务
```

首条命令生成含学生、题号和版本哈希的模板。真实提交声明`origin=student`；人工示范保留`test_fixture`，不得进入画像。接收不调用模型、不执行Java，也不改变题目待复核状态。更新需另加`--execute`；历史追加改用`--records`，不得重复追加已有证据。

### Java验证

用例文件是列表，每项含`input`、`expected_output`和独立推导`basis`。不是直接信任模型自给的答案。

```json
[{"input":"1 2\n","expected_output":"3\n","basis":"独立核算1+2=3，输出含换行"}]
```

```powershell
python verify_java.py --generation-run runs/某出题任务 --cases cases.json --java-home "C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr"
python verify_submission.py --submission-run runs/某接收任务 --cases cases.json --java-home "C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr"
```

默认打印审阅包；逐份审阅并获得具体代码执行授权后，再加`--execute --approve-sha256 <完整哈希> --output runs/新验证任务`。源码、用例、工具或限制变化须重新批准。历史学生记录用`--records`并按需指定`--record-id`。

轻量执行不是文件/网络沙箱，不用于无人值守任意代码。限制为编译20秒、单例3秒、输出64KiB、堆128MiB、元空间64MiB、最多20例；首次失败停止，未执行不算失败。CRLF归一后精确比较，不支持任意合法答案判题。

保存approval.json、verification.json和SQLite/trace；完成恢复不重跑，未知执行不自动重启。临时Java源码/class自动清理，证据不删除。参考答案验证不能当学生表现。

画像入口加`--verification-run <学生验证任务>`（可重复）附加绑定结果。模型仅接收全通过摘要，或失败数量及失败输入、预期/实际输出与异常；完整日志保留。有限通过不证明全面正确或掌握。

### 题目使用记录

`run_recommendation.py`和教师出题可加`--usage-history <清单>`。路径相对清单文件，清单仅覆盖列出的同学生任务，由调用方添加新记录。

```json
{
  "schema": "question-usage-sources-v1",
  "student_id": "java_u520480375",
  "generation_runs": ["../../runs/generation-chain-001"],
  "submission_runs": [],
  "verification_runs": []
}
```

本地实验区有`artifacts/usage-history/java_u520480375.json`示例（不随仓库分发）；新使用者可按上面的结构创建自己的清单。接收真实作答后列入submission_runs；verification_runs只能放匹配提交的学生验证任务。重复路径去重，错学生、错版本、篡改及fixture拒绝。

模型读取相关公开题面、收到的作答记录、已有模型观察及精简运行结果；不据此自动更新画像。生成不等于已交付，未收到作答不等于未做。推荐到出题继承同一快照，不另换清单；未提供清单时预览会明确显示。

## 恢复与验证

`--status/--trace --output <任务>`只读；`--resume`必须沿用原输入、模型、实现和预算。SQLite是权威状态，trace.json可能落后。已成功步骤复用，收到的响应本地重放，结果未知不自动重发；同一步最多两次尝试，恢复不重置限额。详见[恢复与排查](docs/reliable-runs.md)。

```powershell
$env:LOCAL_JAVA_TEST_HOME="C:/Program Files/JetBrains/PyCharm 2024.3.4/jbr"
python -m unittest discover -s tests
```

测试使用临时假模型和受控固定程序，不调用付费API。工程测试通过不等于内容准确率或学习效果。

## 文档与剩余工作

- [当前方案与验收边界](docs/recommendation-plan.md)：含六维审核标准、接口语义和剩余事项。
- [复用与许可登记](docs/reference-code.md)：保留上游来源、版本及MIT声明；不称论文复现。
- 本地《目前进展.docx》与《项目实验记录.docx》：分别记录方法进展和实验事实，位于仓库外的 `outputs/paper-workflows-20261012/`，不随 Git 仓库分发。

当前可在本地浏览已保存的系统结果；技能进化四方案试验已在独立实验区完成，尚未成为正式出题流程。后续研究选择以最新实验材料为准，早期计划中的“尚未开展”只描述当时状态。旧归因纠错、自动用例、Docker、真实教学试验不属于当前必须补齐的模块。

## 分享与许可

仓库只包含代码、配置模板、测试和说明；不包含密钥、原始学生数据、模型响应、检查点和运行日志。代码可见不等于已授予任意再分发许可：自有部分暂未选定开源许可证；第三方提示词的 MIT 声明保留于 `profiling/LICENSE`，适用范围见 [复用登记](docs/reference-code.md)。
