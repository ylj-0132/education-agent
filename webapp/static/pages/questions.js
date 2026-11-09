import { state, filtered } from '../state.js';
import { el, tag, hero, panel, empty, openDetail, rows, $ } from '../ui.js';
import { artifact } from '../api.js';
import { dimensions } from '../labels.js';
export async function showQuestion(item) {
  const epoch = state.epoch;
  const g = await artifact(item.path);
  if (epoch !== state.epoch) return;
  const v = g.versions?.at(-1);
  const nodes = [];
  nodes.push(el('p', 'small muted', `${g.student_id} · ${item.label}`));
  if (!v) {
    openDetail('生成任务没有题目', [
      empty(g.error ? '任务执行失败，请检查日志。' : '没有可用题目版本。'),
    ]);
    return;
  }
  nodes.push(
    tag(g.status),
    el('p', 'note', '生成题尚待人工复核。模型审核通过或参考代码用例通过都不会自动发布题目。'),
  );
  const q = v.question;
  nodes.push(el('h2', 'question-title', q.title || '需要拆分目标'));
  for (const [label, key] of [
    ['题目要求', 'statement'],
    ['输入格式', 'input_format'],
    ['输出格式', 'output_format'],
    ['约束与边界', 'constraints'],
  ])
    if (q[key]) nodes.push(el('h3', '', label), el('p', 'question-body', q[key]));
  for (const s of q.samples || []) {
    nodes.push(
      el('h3', '', '样例'),
      el('pre', '', `输入\n${s.input}\n输出\n${s.output}`),
      el('p', 'small', s.explanation),
    );
  }
  const checks = el('div', 'review-grid');
  for (const c of v.review?.checks || []) {
    const x = el('div', 'review-check');
    x.append(
      el('strong', '', dimensions[c.dimension] || c.dimension),
      tag(c.status),
      el('p', '', c.reason),
      el('p', 'mono small', c.location),
    );
    checks.append(x);
  }
  nodes.push(el('h3', '', '六维审核 · 版本 ' + v.version), checks);
  if (q.reference_code) {
    const details = el('details');
    details.append(
      el('summary', '', '查看参考代码与解析'),
      el('pre', '', q.reference_code),
      el('p', 'question-body', q.explanation),
    );
    nodes.push(details);
  }
  nodes.push(
    el('p', 'mono small muted', `题号 ${g.question_id}\n版本哈希 ${v.question_sha256 || '未记录'}`),
  );
  openDetail(q.title || '题目详情', nodes);
}

export async function questions(content) {
  content.append(
    hero(
      '练习目标、生成题与审核',
      '浏览已有题面与六维审核；检查目标、理由和版本。模型一致意见不替代独立核验。',
      '结果浏览',
    ),
  );
  const grid = el('div', 'grid'),
    a = panel('生成题任务'),
    b = panel('目标推荐');
  a.append(rows(filtered(state.data.generations), showQuestion));
  b.append(
    rows(filtered(state.data.recommendations), async (item) => {
      const r = await artifact(item.path);
      openDetail('练习目标推荐', [
        tag(r.status),
        el('pre', '', JSON.stringify(r.decision, null, 2)),
        el('p', 'note', '目标推荐是模型推断，不自动改变学生状态。'),
      ]);
    }),
  );
  grid.append(a, b);
  content.append(grid);
  const v = panel('已有 Java 验证记录');
  v.append(
    rows(filtered(state.data.verification_runs), async (item) => {
      const epoch = state.epoch;
      const r = await artifact(item.path);
      if (epoch !== state.epoch) return;
      const cases = r.cases || [];
      openDetail('有限用例验证', [
        tag(r.status),
        el(
          'p',
          '',
          `实际执行 ${cases.length} 例，通过 ${cases.filter((c) => c.passed).length} 例。`,
        ),
        el('p', 'note', '仅对绑定源码及所选用例成立；不等于全面正确、学生掌握或题目发布批准。'),
        el('pre', '', JSON.stringify({ compile: r.compile, cases: r.cases }, null, 2)),
      ]);
    }),
  );
  content.append(v);
}
