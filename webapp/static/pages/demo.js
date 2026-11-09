import { state } from '../state.js';
import { el, tag, button, hero, panel, empty, documentLink, $ } from '../ui.js';
import { artifact } from '../api.js';
import { dimensions } from '../labels.js';
import { render, navigate, presentationPath } from '../router.js';
import { SavedDemo } from '../demo-route.js';
import { showQuestion } from './questions.js';
export async function savedDemo(content, epoch) {
  content.append(
    hero(
      '案例分析：从学习证据到练习生成',
      '选取一名学生的已保存结果，说明系统如何将历史作答转化为下一轮练习。',
      '系统方法 / 实际案例',
    ),
  );
  content.append(
    el(
      'p',
      'small muted',
      `案例学生：${SavedDemo.student} · 本页展示固定记录，学生筛选不适用 · 只读浏览`,
    ),
  );
  let p, r, g, evidence;
  try {
    [p, r, g] = await Promise.all(SavedDemo.paths.map(artifact));
    evidence = SavedDemo.validate(p, r, g, SavedDemo.student);
  } catch (e) {
    if (epoch === state.epoch)
      content.append(
        empty(
          '演示记录暂不可用：' + e.message + ' 本页需要本机已有结果，克隆代码不会附带学生数据。',
        ),
      );
    return;
  }
  if (epoch !== state.epoch) return;
  content.append(presentationPath('demo'));
  content.append(documentLink('历史数据到画像与目标推荐的执行流程；出题审核与修订的实际控制流程'));
  const titles = ['原始作答', '学生画像', '练习目标', '生成题目', '六维审核'];
  const step = state.demoStep || 0;
  const tabs = el('div', 'choices');
  tabs.setAttribute('aria-label', '演示步骤');
  titles.forEach((title, i) => {
    const b = button(
      `${i + 1}. ${title}`,
      () => {
        state.demoStep = i;
        return render();
      },
      i === step ? 'active' : '',
    );
    if (i === step) b.setAttribute('aria-current', 'step');
    tabs.append(b);
  });
  content.append(tabs);
  const box = panel(`${String(step + 1).padStart(2, '0')} / ${titles[step]}`);
  const methods = [
    [
      '训练集中的题目与学生代码',
      '保留学生、题号、数据划分及来源；测试集不用于画像。推荐引用具体作答，供后续回查。',
      '具有来源标识的学习证据',
      '让画像判断能够对应到学生实际写过的代码。',
    ],
    [
      '历史作答与公共知识点目录',
      '逐条提取知识点表现，再按知识点汇总；每条判断保留证据引用。公共知识结构与个人状态分开存储。',
      '知识点评价、局部表现与对应证据',
      '避免把一次作答错误直接当成稳定的知识缺陷。',
    ],
    [
      '相关画像、允许的知识范围与显式题目使用记录',
      '一次模型调用提出知识点或组合，程序校验范围、引用及结构。条件先修作为上下文，不设固定组合大小。',
      '目标知识点、推荐理由和证据引用',
      '使推荐目标来自学习证据，并可直接交给出题环节。',
    ],
    [
      '目标知识点、相关证据、学生画像及已提供的练习历史',
      '模型同时生成题面、样例、参考代码和解析；要求练习目标得到体现，并避免原题及仅改数值或场景的近重复题。',
      '一道待审核的目标练习',
      '将“练什么”转化为可作答的具体任务。教师也可以直接指定目标。',
    ],
    [
      '生成题、参考答案、解析与目标上下文',
      '按题意、正确性、目标匹配、学生适配、解析质量和组合合理性检查；按反馈最多修订一次，再次审核。',
      '六维审核结果与保存的题目版本',
      '在交付前发现内容问题；模型审核仍可能漏错，需要人工复核。',
    ],
  ];
  const method = methods[step],
    explanation = el('section', 'method-explanation');
  const dl = el('dl', 'method-spec');
  for (const [label, value] of [
    ['输入', method[0]],
    ['处理方法', method[1]],
    ['输出', method[2]],
  ])
    dl.append(el('dt', '', label), el('dd', '', value));
  explanation.append(dl, el('p', 'method-purpose', method[3]));
  box.append(explanation, el('h3', 'result-heading', '实际结果'));

  const ids = r.decision.concept_ids || [];
  const concept = (id) => p.concepts?.[id] || state.data.catalog.concepts?.[id] || id;
  const v = g.versions.at(-1),
    q = v.question;
  if (step === 0) {
    box.append(
      el('p', '', '下面是本次推荐实际引用的历史作答。画像记录判断，原始代码用于回查判断依据。'),
    );
    for (const [index, e] of evidence.entries()) {
      const d = el('details');
      d.open = index === 0;
      d.append(
        el('summary', '', `原始作答 · ${e.problem_id || e.record_id}`),
        el('p', 'question-body', e.question),
        el('pre', '', e.program),
        el('p', 'mono small muted', e.record_id),
      );
      box.append(d);
    }
  }
  if (step === 1) {
    box.append(
      el(
        'p',
        '',
        `此画像保存 ${p.evidence?.length || 0} 份证据。下面聚焦本次推荐涉及的知识点；判断属于模型推断。`,
      ),
    );
    for (const id of ids) {
      const c = el('article', 'concept');
      c.append(
        el('h3', '', concept(id)),
        el('p', '', p.summaries?.[id]?.evaluation || '没有此项汇总。'),
      );
      for (const o of (p.observations || []).filter(
        (o) => o.concept_id === id && r.decision.evidence_ids.includes(o.evidence_id),
      )) {
        c.append(
          tag(o.status),
          el('p', '', o.description),
          el('p', 'mono small muted', o.evidence_id),
        );
      }
      box.append(c);
    }
    box.append(
      button('查看完整画像', () => {
        state.student = SavedDemo.student;
        $('#student').value = state.student;
        state.selectedProfile = SavedDemo.paths[0];
        return navigate('profiles');
      }),
    );
  }
  if (step === 2) {
    box.append(
      el('h3', '', ids.map(concept).join(' · ')),
      el('p', 'question-body', r.decision.reason),
    );
    for (const u of r.decision.uncertainties || []) box.append(el('p', 'note', u));
  }
  if (step === 3) {
    box.append(tag(g.status), el('h3', 'question-title', q.title));
    for (const [label, key] of [
      ['题目要求', 'statement'],
      ['输入格式', 'input_format'],
      ['输出格式', 'output_format'],
      ['约束', 'constraints'],
    ])
      if (q[key]) box.append(el('h3', '', label), el('p', 'question-body', q[key]));
    for (const sample of q.samples || [])
      box.append(
        el('h3', '', '样例'),
        el('pre', '', `输入\n${sample.input}\n输出\n${sample.output}`),
        el('p', '', sample.explanation || ''),
      );
  }
  if (step === 4) {
    box.append(
      el('p', '', `展示保存的第 ${v.version} 版审核；本任务共保存 ${g.versions.length} 个版本。`),
    );
    const checks = el('div', 'review-grid');
    for (const c of v.review?.checks || []) {
      const item = el('div', 'review-check');
      item.append(
        el('strong', '', dimensions[c.dimension] || c.dimension),
        tag(c.status),
        el('p', '', c.reason),
        el('p', 'small muted', c.location || ''),
      );
      checks.append(item);
    }
    box.append(
      checks,
      el(
        'p',
        'note',
        '生成题仍待人工复核。模型审核不是独立判题，也没有真实学生完成这道新题后的学习效果证据。',
      ),
    );
  }
  if (step >= 3)
    box.append(
      button('查看题目、参考代码与完整审核', () =>
        showQuestion({ path: SavedDemo.paths[2], label: '主展示案例 · 固定版本' }),
      ),
    );
  const source =
    step < 2 ? SavedDemo.paths[0] : step === 2 ? SavedDemo.paths[1] : SavedDemo.paths[2];
  const provenance = el('details', 'provenance');
  provenance.append(el('summary', '', '查看结果文件来源'), el('p', 'mono small muted', source));
  box.append(provenance);
  content.append(box);
  const nav = el('div', 'actions');
  if (step === 4) nav.append(button('继续：技能进化实验 →', () => navigate('skills'), 'secondary'));
  const prev = button('← 上一步', () => {
    state.demoStep = step - 1;
    return render();
  });
  prev.disabled = step === 0;
  const next = button(
    step === 4 ? '回到原始作答' : '下一步 →',
    () => {
      state.demoStep = (step + 1) % 5;
      return render();
    },
    'primary',
  );
  nav.append(prev, el('span', 'muted small', `${step + 1} / 5`), next);
  content.append(nav);
}
