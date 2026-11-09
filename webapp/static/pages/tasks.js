import { state } from '../state.js';
import { el, tag, button, hero, panel, openDetail, $, notice } from '../ui.js';
import { api, artifact } from '../api.js';
import { opNames, labels } from '../labels.js';
import { render } from '../router.js';
import { pollJob } from '../job-poller.js';
export function fieldControl(field) {
  const name = field.name,
    label = labels[name] || name;
  const wrap = el(
    'label',
    'field ' + (['requirement', 'approve_sha256', 'record_id'].includes(name) ? 'wide' : ''),
  );
  wrap.append(el('span', '', label));
  let input;
  const source = state.data[field.source];
  if (['scope', 'targets', 'goals'].includes(name)) {
    input = el('select');
    input.multiple = true;
    for (const [id, title] of Object.entries(state.data.catalog.concepts || {}))
      input.append(new Option(title + ' · ' + id, id));
  } else if (source) {
    input = el('select');
    input.multiple = field.multiple;
    if (!field.multiple) input.append(new Option('不指定 / 选择记录', ''));
    for (const item of source.filter(
      (v) =>
        (!state.student ||
          v.student_id === state.student ||
          (name === 'records' && v.student_ids?.includes(state.student))) &&
        (name !== 'verification_run' || v.verification_kind === 'student') &&
        (name !== 'submission_run' || v.learning_evidence_eligible === true),
    ))
      input.append(new Option(item.label + ' — ' + (item.student_id || item.path), item.path));
  } else if (field.multiple) {
    input = el('textarea');
    input.placeholder = '每行一项';
  } else {
    input = el('input');
    input.type = ['limit', 'max_calls'].includes(name) ? 'number' : 'text';
    if (name === 'student') input.value = state.student;
    input.placeholder =
      name === 'cases'
        ? '例如 artifacts/student-java-20261030/cases.json'
        : name === 'submission'
          ? '仓库内作答 JSON 的相对路径'
          : name === 'catalog' || name === 'concepts'
            ? '默认 config/java-basics.json'
            : '';
  }
  input.name = name;
  input.dataset.multiple = String(field.multiple);
  wrap.append(input);
  if (input.multiple) wrap.append(el('small', '', '可按 Ctrl / Command 多选；留空表示不指定。'));
  return wrap;
}

export function fieldsFrom(form) {
  const fields = {};
  for (const node of form.elements) {
    if (!node.name || node.name === 'confirm') continue;
    let value;
    if (node.dataset.multiple === 'true') {
      value =
        node.tagName === 'SELECT'
          ? [...node.selectedOptions].map((o) => o.value)
          : node.value
              .split('\n')
              .map((x) => x.trim())
              .filter(Boolean);
    } else value = node.value.trim();
    if (value.length) fields[node.name] = value;
  }
  return fields;
}

export async function tasks(content) {
  const epoch = state.epoch;
  content.append(
    hero(
      '任务执行',
      '先核对输入与预览，再启动模型调用、保存作答或批准运行。所有任务复用现有命令行入口。',
      '运行操作',
    ),
  );
  const choices = el('div', 'choices');
  for (const op of state.data.operations)
    choices.append(
      button(
        opNames[op.id] || op.id,
        () => {
          state.operation = op.id;
          state.preview = null;
          return render();
        },
        state.operation === op.id ? 'active' : '',
      ),
    );
  content.append(choices);
  const op =
    state.data.operations.find((o) => o.id === state.operation) || state.data.operations[0];
  const grid = el('div', 'task-grid'),
    p = panel(opNames[op.id]),
    jobsPanel = panel('任务状态');
  const form = el('form');
  form.addEventListener('submit', (e) => e.preventDefault());
  const fields = el('div', 'form-grid');
  for (const f of op.fields) fields.append(fieldControl(f));
  form.append(fields);
  if (op.id === 'generate')
    form.append(
      el(
        'p',
        'note',
        '推荐入口只选择正式 B 推荐任务；直接目标入口填写学生、范围、目标与教学要求。两种入口不能混用。',
      ),
    );
  if (op.id === 'submit') {
    const tools = el('div', 'actions');
    tools.append(button('从所选生成题填写作答草稿', () => draftAnswer(form)));
    form.append(
      tools,
      el(
        'p',
        'small muted',
        '提交来源默认为 test_fixture。只有实际学生提交才能声明 origin=student；来源声明不是身份认证。',
      ),
    );
  }
  if (op.id.startsWith('verify_'))
    form.append(
      el(
        'p',
        'note',
        '预览会列出源码、用例、工具与批准哈希。逐份审阅后填写批准哈希；本地执行不是文件或网络沙箱。',
      ),
    );
  const confirm = el('label', 'confirmation');
  const check = el('input');
  check.type = 'checkbox';
  check.name = 'confirm';
  confirm.append(
    check,
    document.createTextNode(
      op.id.startsWith('verify_')
        ? '我已逐份审阅源码、独立用例和工具，批准本次确切哈希执行。'
        : op.id === 'submit'
          ? '我已核对学生、题目版本和作答来源，确认保存。'
          : '我确认本次输入及请求上限，授权此任务调用配置的付费模型。',
    ),
  );
  form.append(confirm);
  const actions = el('div', 'actions');
  const preview = button('1. 预览输入', () => start('preview'), 'secondary'),
    execute = button(
      op.id === 'submit'
        ? '2. 保存作答'
        : op.id.startsWith('verify_')
          ? '2. 批准并执行'
          : '2. 启动任务',
      () => start(op.id === 'submit' ? 'save' : 'execute'),
      'primary',
    );
  execute.disabled = true;
  actions.append(preview, execute);
  form.append(actions);
  p.append(form);
  jobsPanel.append(
    el(
      'p',
      'small muted',
      '命令完成不代表审核通过。请继续查看结果中的题目状态、用例结果或失败原因。',
    ),
    el('div', '', ''),
  );
  const list = el('div');
  jobsPanel.append(list);
  grid.append(p, jobsPanel);
  content.append(grid);
  const signature = () => {
    const fields = fieldsFrom(form);
    delete fields.approve_sha256;
    return JSON.stringify(fields);
  };
  let previewSignature = null;
  form.addEventListener('input', () => {
    execute.disabled = !(check.checked && previewSignature === signature());
  });
  form.addEventListener('change', () => {
    execute.disabled = !(check.checked && previewSignature === signature());
  });
  async function start(mode) {
    if (mode !== 'preview' && (!check.checked || previewSignature !== signature()))
      throw Error('请先完成相同输入的预览，并明确确认本次操作。');
    preview.disabled = true;
    execute.disabled = true;
    try {
      const fields = fieldsFrom(form);
      const before = signature();
      const result = await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({ operation: op.id, mode, fields, confirm: mode !== 'preview' }),
      });
      state.job = result.job;
      showJob(result.job);
      await poll(
        result.job.id,
        (job) => {
          showJob(job);
          if (job.mode === 'preview' && job.status === 'succeeded') {
            previewSignature = before;
            execute.disabled = !(check.checked && before === signature());
          }
        },
        epoch,
      );
    } finally {
      preview.disabled = false;
    }
  }
  function showJob(job) {
    list.replaceChildren();
    const row = el('div', 'row');
    row.append(el('strong', '', opNames[job.operation] || job.operation), tag(job.status));
    list.append(
      row,
      el('p', 'mono small muted', job.output || ''),
      el('pre', 'job-log', job.log || '等待本地命令返回…'),
    );
    if (job.error) list.append(el('p', 'note', job.error));
  }
  const jobs = await api('/api/jobs');
  if (epoch !== state.epoch) return;
  if (jobs.jobs.length) {
    showJob(jobs.jobs[0]);
    if (['queued', 'running'].includes(jobs.jobs[0].status))
      poll(jobs.jobs[0].id, showJob, epoch).catch(notice);
  }
}

export async function poll(id, callback, epoch = state.epoch) {
  return pollJob(
    id,
    async (id) => (await api('/api/jobs/' + encodeURIComponent(id))).job,
    (job) => {
      state.job = job;
      callback(job);
    },
    () => epoch === state.epoch,
    () => new Promise((r) => setTimeout(r, 1200)),
  );
}

export async function draftAnswer(form) {
  const epoch = state.epoch;
  const path = form.elements.generation_run.value;
  if (!path) throw Error('请先选择生成题任务。');
  const g = await artifact(path);
  if (epoch !== state.epoch) return;
  const v = g.versions?.at(-1);
  if (!v?.question_sha256) throw Error('该任务没有可绑定的题目版本。');
  const wrap = el('div');
  wrap.append(
    el(
      'p',
      'note',
      '粘贴学生实际代码并填写唯一提交 ID。演示代码必须保留 test_fixture，不会进入真实画像。',
    ),
  );
  const id = el('input');
  id.placeholder = '唯一 submission_id';
  const origin = el('select');
  origin.append(
    new Option('测试示范 test_fixture', 'test_fixture'),
    new Option('真实学生来源声明 student', 'student'),
  );
  const program = el('textarea');
  program.style.width = '100%';
  program.style.minHeight = '240px';
  program.placeholder = '粘贴 Java 代码原文';
  const status = el('p', 'small');
  const fields = el('div', 'form-grid');
  for (const [label, node] of [
    ['提交 ID', id],
    ['来源声明', origin],
    ['原始代码', program],
  ]) {
    const l = el('label', 'field wide');
    l.append(el('span', '', label), node);
    fields.append(l);
  }
  wrap.append(
    fields,
    status,
    button(
      '保存输入草稿并带入表单',
      async () => {
        if (!id.value.trim() || !program.value.trim()) throw Error('请填写提交 ID 和代码。');
        const answer = {
          schema: 'student-answer-v1',
          submission_id: id.value.trim(),
          student_id: g.student_id,
          question_id: g.question_id,
          question_sha256: v.question_sha256,
          origin: origin.value,
          program: program.value,
          submitted_at: null,
        };
        try {
          const r = await api('/api/submission-draft', {
            method: 'POST',
            body: JSON.stringify({ answer }),
          });
          form.elements.submission.value = r.path;
          form.dispatchEvent(new Event('input'));
          $('#detail').close();
        } catch (e) {
          status.textContent = e.message;
        }
      },
      'primary',
    ),
  );
  openDetail('准备作答输入', [wrap]);
}
