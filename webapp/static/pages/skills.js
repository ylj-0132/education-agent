import { el, hero, panel, documentLink } from '../ui.js';
import { presentationPath } from '../router.js';
import { skillResults as resultRows } from '../data/skill-results.js';
export function skillResults(content) {
  content.append(
    hero(
      '出题技能进化：最终实验结果',
      '比较四种外部文本技能的改进方式，观察它们在新出题任务上的表现。',
      '独立实验 / 2026.11.05',
    ),
    presentationPath('skills'),
    documentLink('四种技能进化方法与实验设计；四方案实验的结果与具体问题'),
  );
  const intro = panel('做了什么');
  intro.append(
    el(
      'p',
      '',
      '将可复用的出题做法写成 skill，用开发任务及反馈修改它，再将选定版本提供给模型生成新题。不训练模型参数。',
    ),
  );
  const flow = el('div', 'experiment-flow');
  for (const [n, t] of [
    ['4', '开发案例 · 修改技能'],
    ['4', '选择案例 · 确定版本'],
    ['8', '新案例 · 最终比较'],
  ]) {
    const item = el('div');
    item.append(el('strong', '', n), el('span', '', t));
    flow.append(item);
  }
  intro.append(
    flow,
    el(
      'p',
      'small muted',
      '每种方法两轮候选；先冻结版本，再测试。基线与四方案共 40 份最终输出，统一使用 Luna。只比较一次生成的完整题目，不接入后续六维审核与内容修订。',
    ),
  );
  content.append(intro);
  const result = panel('新案例上的结果');
  result.append(el('p', 'small muted', '指标：8 份输出中，静态审阅未发现确定缺陷的数量。'));
  const table = el('table', 'method-table results-table'),
    head = el('thead'),
    hr = el('tr');
  for (const t of ['方案 / 核心做法', '最终技能', '严格字面口径', '允许输入缓冲'])
    hr.append(el('th', '', t));
  head.append(hr);
  table.append(head);
  const body = el('tbody');

  for (const [name, method, version, strict, buffer] of resultRows) {
    const tr = el('tr', name === 'WikiSkill' ? 'highlight-row' : '');
    const label = el('td');
    label.append(el('strong', '', name), el('small', '', method));
    tr.append(
      label,
      el('td', '', version),
      el('td', 'score', `${strict} / 8`),
      el('td', 'score', `${buffer} / 8`),
    );
    body.append(tr);
  }
  table.append(body);
  const wrap = el('div', 'method-table-wrap');
  wrap.append(table);
  result.append(
    wrap,
    el('p', 'result-takeaway', 'WikiSkill 在本次新案例比较中结果最好，作为下一轮研究的优先候选。'),
  );
  result.append(
    el(
      'p',
      'small muted',
      '两列对应“不使用数组”是否允许 byte[] 输入缓冲的不同解释。Skill∞ 的 4 份中另有 1 份空行表示不确定。结果来自匿名静态审阅，未执行 Java；每任务仅一次输出，各方案成本不同，不能据此认定稳定优势或教学效果。',
    ),
  );
  content.append(result);
  const wiki = panel('WikiSkill 如何形成最终技能');
  wiki.append(
    documentLink('WikiSkill的改进过程与下一步研究'),
    el(
      'p',
      'wiki-chain',
      '开发任务与反馈 → 维护经验 wiki → 改写出题技能 → 选择并冻结版本 → 应用于新题',
    ),
  );
  const comparison = el('div', 'grid');
  for (const [title, text] of [
    ['初始技能', '检查教师要求是否落实，以及题面、解法、代码和样例是否一致。'],
    [
      '最终技能中的具体规则',
      '教师明确要求数组、Map、排序或逐字符循环时，必须在题面或约束中写明，不能仅体现在参考代码或解析中。',
    ],
  ]) {
    const part = el('div', 'skill-text');
    part.append(el('h3', '', title), el('p', '', text));
    comparison.append(part);
  }
  wiki.append(
    comparison,
    el(
      'p',
      'small muted',
      '以上为技能要点概述。出题时只提供选中的技能，不提供整个 wiki；WikiSkill 最终采用第 2 轮版本。Skill∞ 最终保留原元技能，不能将其结果归因为元技能自我改进。',
    ),
  );
  content.append(wiki);
  const source = el('details', 'supporting-method');
  source.append(
    el('summary', '', '实验口径与资料来源'),
    el(
      'p',
      '',
      '本页为截至 2026-11-05 的固定结果摘要，数值核对自 final-results.json。原始输出与评审记录仍保留在本地独立实验区。',
    ),
    el('p', 'mono small', 'experiments/skill-four-20261104/final-results.json'),
    el(
      'p',
      'small',
      '对应《目前进展》及《四方案技能进化实验结果》。本试验为论文方法的缩小改编，尚未接入正式出题流程。',
    ),
  );
  content.append(source);
}
