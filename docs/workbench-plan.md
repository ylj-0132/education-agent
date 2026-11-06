# 本地工作台实施计划

目标：复用现有命令行闭环，提供可查看真实运行材料、显式预览及启动任务的本地前端。论文方向仅研究和讨论，本轮不开展付费实验。

约束：Python 标准库后端，原生 HTML/CSS/JS；只监听 127.0.0.1；不公开部署；不读取或返回密钥；不执行学生 Java 或付费模型进行开发测试；旧产物不修改；不自动发布题目，不把 fixture 作为真实作答。

## 任务 1 本地后端

新增 webapp/server.py、webapp/service.py、serve_workbench.py、tests/test_workbench.py。

API 约定：GET /api/bootstrap 返回 csrf_token、catalog、profiles、generations、recommendations、record_files、usage_files、submission_runs、verification_runs；各任务项包含 path、student_id（适用时）、status、label。GET /api/artifact?path= 相对路径只允许白名单内已知 JSON 结果，不允许 .env、原始 trace/request 或任意文件。GET /api/jobs 返回 jobs；GET /api/jobs/<id> 返回 job。

POST /api/jobs 需 X-Workbench-Token，JSON 为 {operation, mode, fields}。operation 为 profile/recommend/generate/update/submit/verify_reference/verify_student；mode 为 preview/execute/save。返回 {job}，job 含 id/status/operation/mode/output/log/error。字段采用既有 CLI 参数名的下划线形式；scope/targets/goals/requirement/verification_run 为数组，其余为字符串。execute/save 必须额外传 confirm=true；Java execute 另需 approve_sha256。输出目录由服务端生成，不允许任意 shell 或自定义 env 文件。每次只运行一个任务，重复忙时拒绝。stdout/stderr 有界并脱敏。

先写测试：路径逃逸与秘密文件拒绝、未知操作拒绝、未确认执行拒绝、Java 无批准拒绝、预览不自动追加执行参数、后台任务成功/失败、并发任务拒绝。使用假执行器，无真实模型/Java。实现后运行针对测试。

## 任务 2 前端

新增 webapp/static/index.html、app.js、style.css。中文教师工作台，暖白、墨绿、衬线标题；侧栏：总览、学生画像、练习与审核、任务工作台。展示真实数据与空态，学生筛选、画像证据追溯、题面/六维审核/版本/待复核状态；任务表单依据 API 参数生成，先预览再明确执行；保留提交来源声明及 Java 哈希批准。反馈真实状态和错误，不能用虚假成功兜底。原始观察不称掌握概率。

验证：JS 语法、后端接口测试、浏览器桌面和窄屏检查；浏览器只操作读取/无副作用预览，不点击执行付费或 Java。

## 任务 3 现有问题及收尾

复现后修补明确缺陷；清除已定推荐问题及 Java 未集成的过时提示，不修改旧实验结果。README 补启动方法与本地访问边界，AGENTS 更新前端已授权约束。研究说明区分已有工程能力、候选贡献、对照实验与缺少的证据，目标 2026 年 11 月内形成投稿判断。

进度：后端与前端已实现。新增入口约束回归、后端测试与任务轮询测试；独立审查提出的编码、学生归属及重新进入任务页的刷新问题已修补。HTTP资源与bootstrap检查成功，浏览器宿主不可用，视觉及实际点击验收仍未完成。原142项全套记录保持历史含义，当前按受影响模块验证。论文候选方向维护在recommendation-plan.md末节，尚未开展新实验。
