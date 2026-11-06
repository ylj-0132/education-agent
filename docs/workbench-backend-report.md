# 本地工作台后端交接

实现：`webapp/service.py`、`webapp/server.py`、`serve_workbench.py`、`tests/test_workbench.py`。Python 标准库，固定监听 127.0.0.1；默认端口 8765。未调用模型，未运行 Java 或学生程序，未修改已有运行结果。

验证：`python -m unittest tests.test_workbench`，9 项通过（最后一次输出：Ran 9 tests in 0.593s, OK）。使用假 CLI runner，HTTP 测试使用临时目录与临时 loopback 端口。覆盖允许清单/越界/秘密文件拒绝、非法操作、确认、Java 哈希、预览不执行、参数注入、并发拒绝、失败、脱敏、输出上限与 Host/CSRF。

## API

- `GET /api/bootstrap`：`{csrf_token,catalog,profiles,generations,recommendations,record_files,usage_files,submission_runs,verification_runs,operations}`。列表项 `{path,student_id,status,label}`；无学生时 student_id 为 null。catalog 是现有知识目录 JSON。operations 为 `[{id,label,fields:[{name,label,type,source,required,multiple}],modes}]`，type 为 path/text，source 为对应 bootstrap 列表名或 null，required 当前均 false（CLI 验证必需组合），multiple 对数组字段为 true。
- `GET /api/artifact?path=...`：`{artifact: 原 JSON}`。仅可查看被 inventory 识别的固定结果名以及 config/java-basics.json；trace/request/.env/任意文件拒绝。
- `GET /api/jobs`：`{jobs:[job]}`；`GET /api/jobs/<id>`：`{job}`。
- `POST /api/jobs`：请求头 `X-Workbench-Token`，Content-Type application/json。请求 `{operation,mode,fields,confirm?,approve_sha256?}`，返回 HTTP 202 `{job}`。operation 为 profile/recommend/generate/update/submit/verify_reference/verify_student。mode 为 preview/execute，submit 为 preview/save。execute/save 必须 confirm=true。Java execute 必须 64 位 approve_sha256（fields 中同名字段也支持）。
- job 为 `{id,status,operation,mode,output,log,error}`。status 为 queued/running/succeeded/failed；output 是服务端分配 runs/workbench-UUID；log 为字符串，error 为 null 或字符串；最多保留 50 条任务。
- 验证/请求错误 HTTP 400，Host/Origin/CSRF 错误 403，不存在 404，并发任务 409；错误体 `{error}`。不返回异常堆栈。

字段为既有 CLI 参数的下划线形式。scope/targets/goals/requirement/verification_run 为字符串数组，其余为字符串；允许字段由 operations metadata 明确给出。路径仅限仓库内 runs/artifacts/config 的 JSON 文件或目录；run 类字段支持已列出结果 JSON 路径并转为父目录。不得指定 env_file、cache、output、java_home、shell。submit 接受已有 submission JSON 相对路径，未扩展为内联答案。

浏览器须用启动输出的 http://127.0.0.1:端口 访问，Host 必须精确匹配；跨 Origin/跨站拒绝，POST 必须 token。静态文件仅 index.html/app.js/style.css。输出流持续排空但只保留有限前缀，并进行常见 credential 形式脱敏；总任务超时 1800 秒。脱敏不是任意自由文本保密证明；原始 trace 不提供给浏览器。

运行进程以当前本地用户身份执行现有脚本，不是程序隔离沙箱。开发测试只使用假 runner。Java 来源和独立用例的审阅及绑定哈希批准仍由既有 CLI 强制校验。

补充交接（最终验证）：`python -m unittest tests.test_workbench`：11 项通过，Ran 11 tests in 0.636s, OK。新增回归覆盖 Java 固定工具路径、usage-history schema、内联作答草稿保存与错误来源拒绝。

新增 `POST /api/submission-draft`，同样要求 token、JSON、Host/Origin 校验；请求 `{answer: student-answer-v1 对象}`，显式 origin 只能 student/test_fixture，必填 submission_id/student_id/question_id/question_sha256/program，哈希格式校验；返回 HTTP 201 `{path: "artifacts/workbench-inputs/UUID.json"}`。这是用户主动保存草稿，不执行模型/Java，不接收为掌握证据。下一步通过 submit 的 submission 路径预览/保存，现有 CLI 执行最终身份和业务校验。草稿大小上限 60000 UTF-8 字节。

Java 工具目录由服务器 LOCAL_JAVA_TEST_HOME 环境或文档中 PyCharm JBR 路径配置，不接收浏览器自定义 executable。usage_files 按 question-usage-sources-v1 schema 筛选。nargs 数组拒绝选项样式字符串；自由 requirement 使用 `--requirement=TEXT` 避免 argparse 参数注入。之前关于不支持内联答案的说明已由本补充替代。

再次复核后的修订：子进程由服务器强制设置 PYTHONIOENCODING=utf-8、PYTHONUTF8=1，新增真正运行无害 Python 中文输出的往返测试（不是 Java/API）。submission 列表从 evidence/packet 提取一致的学生身份，新增 learning_evidence_eligible 布尔值（明确 student 来源、已有回执声明为 true 且身份一致才为 true）。verification 列表新增 verification_kind=student/reference；student 从 approval.question.student_evidence 提取，reference 通过 question_id 与唯一 generation 学生关联。所有列表项新增 identity_verified；无法唯一确定的身份为 null/false，不能当成通用学生来源。

record_files 新增识别 new-evidence.json，所有记录必须有 question/program，否则不列出。记录列表新增 student_ids（多个学生时 student_id 为 null，可供明确过滤）。最终针对性命令 `python -m unittest tests.test_workbench`：13 项通过，Ran 13 tests in 0.703s, OK。未执行 Java 或模型。
