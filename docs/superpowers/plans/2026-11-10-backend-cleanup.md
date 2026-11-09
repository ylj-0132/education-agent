# 本地服务代码整理 Implementation Plan

> 按 executing-plans 在当前会话逐项执行；用户已授权整理现有代码。

**Goal:** 拆开本地服务的结果存取、命令参数校验和任务执行职责，保持现有接口与运行行为。

**Architecture:** `server.py` 负责 HTTP，`service.py` 作为统一入口，委托 `artifacts.py`、`commands.py`、`jobs.py`。继续调用原有 CLI，不引入新服务或框架。

**Tech Stack:** Python 3.12 标准库、unittest。

## 约束

- 不调用付费 API，不运行学生 Java，不改模型提示、核心工作流或旧结果。
- 保留现有 HTTP 路径、响应字段、命令行入口、串行任务限制与批准要求。
- 历史恢复脚本和绑定实现哈希的文件保持原位；不批量格式化核心代码。
- 只整理我们现有后端，不为同学指定前端方案。

## 任务

- [x] 检查 `webapp/service.py`、`server.py` 和现有测试；基线 13 项通过。
- [x] 在 `tests/test_workbench.py` 补充模式分离、运行目录转换、重复参数和批准哈希的行为检查。
- [x] 将结果目录索引、白名单读取和草稿保存移至 `webapp/artifacts.py` 的 `ArtifactStore`。
- [x] 将操作元数据和参数校验移至 `webapp/commands.py`，由 `build_command(artifacts, data, job_id)` 返回参数列表及输出路径；不启动进程。
- [x] 将子进程、有限日志、串行任务状态移至 `webapp/jobs.py` 的 `JobQueue`；保留 runner 注入。
- [x] `WorkbenchService` 保留原公开方法；整理 HTTP 代码排版，不改路由。
- [x] README 补充当前后端职责表，说明 CLI 与本地服务的关系。
- [x] 运行工作台及相关离线测试、JS 测试和 `git diff --check`；核对实际结果目录索引及未修改的核心代码。

## 验证命令

```powershell
python -m unittest discover -s tests -p test_workbench.py
python -m unittest discover -s tests -p test_current_contract.py
node --test tests/test_job_poller.cjs tests/test_demo_route.cjs
git diff --check
```

预期全部通过。工作台任务测试注入假执行器，仅 Unicode 输出测试运行一段固定 Python 打印程序。新旧实现的实际目录索引和命令构造另作只读对照。

## 验证结果（2026-11-10）

- 工作台 16 项、现有流程契约 3 项、JavaScript 5 项通过。
- 新旧实现的实际结果目录索引、学生归属、操作元数据一致；768 组命令参数及错误结果一致。
- `server.py` 新旧 Python 语法树相同，HTTP 行为未改；核心流程、CLI、提示词未修改。
- 本次未重新跑付费实验或 Java；未改动展示页面，未做浏览器视觉验收。
