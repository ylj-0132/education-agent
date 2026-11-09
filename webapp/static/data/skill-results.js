// Fixed 2026-11-05 experiment summary; not live backend output.
// Source: experiments/skill-four-20261104/final-results.json (local only).
export const skillResults = [
  ['基线', '原出题提示 + 共用初始技能', '初始技能', 2, 3],
  ['WikiSkill', '任务反馈 → 经验 wiki → 改写技能', '第 2 轮', 5, 6],
  ['SkillRevise', '诊断问题 → 按固定原则局部改写', '第 1 轮', 3, 3],
  ['Skill∞', '比较元技能 → 用选中元技能改写任务技能', '第 2 轮 · 原元技能', 4, 4],
  ['WikiSkill + Hippo', '同任务双份输出对照 → 更新 wiki 与技能', '第 1 轮', 4, 4],
];
