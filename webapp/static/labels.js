// Display labels; operation IDs remain the backend contract.
export const names = {
  skills: '技能进化实验',
  demo: '案例分析',
  overview: '研究概览',
  profiles: '学生画像',
  questions: '练习与审核',
  tasks: '任务工作台',
};

export const opNames = {
  profile: '建立学生画像',
  recommend: '推荐练习目标',
  generate: '生成与审核题目',
  update: '增量更新画像',
  submit: '接收学生作答',
  verify_reference: '验证参考代码',
  verify_student: '验证学生代码',
};

export const labels = {
  records: '作答记录文件',
  concepts: '知识目录',
  catalog: '知识目录',
  student: '学生 ID',
  verification_run: '学生验证记录',
  limit: '本次作答数量（1–5）',
  max_calls: '最大请求次数（含补试）',
  profile: '参考画像',
  base_profile: '基线画像',
  usage_history: '题目使用清单',
  scope: '允许的知识范围',
  targets: '直接指定的目标',
  goals: '学习目标（可选）',
  requirement: '教学要求（每行一项）',
  recommendation_run: '正式 B 推荐任务',
  generation_run: '生成题任务',
  submission_run: '已接收作答任务',
  submission: '作答 JSON 文件',
  cases: '独立用例 JSON 文件',
  record_id: '指定原始记录 ID',
  approve_sha256: '审阅后批准的完整哈希',
};

export const dimensions = {
  clarity: '题意清晰完整',
  correctness: '可解性与答案正确性',
  target_alignment: '目标匹配',
  student_fit: '难度与学生适配',
  explanation: '解析质量',
  combination: '组合合理性',
};

export const statusNames = {
  completed: '执行完成',
  needs_review: '待人工复核',
  failed: '执行失败',
  partial: '部分完成',
  recommended: '已推荐',
  insufficient_evidence: '证据不足',
  tests_passed: '所选用例通过',
  tests_failed: '存在失败用例',
  compile_failed: '编译未成功',
  received: '已接收',
  rejected: '审核未通过',
  needs_split: '需拆分目标',
  succeeded: '命令完成',
  queued: '排队中',
  running: '运行中',
  pass: '通过',
  fail: '未通过',
  uncertain: '不确定',
  not_applicable: '不适用',
  available: '可读取',
  Good: '本题正面表现',
  Bad: '本题问题表现',
  Unknown: '证据不足',
};
