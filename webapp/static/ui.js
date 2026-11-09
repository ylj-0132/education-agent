import { statusNames } from './labels.js';
export const $ = (s) => document.querySelector(s);

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

export function tag(status) {
  return el(
    'span',
    'tag ' +
      (['pass', 'succeeded', 'completed', 'recommended', 'tests_passed', 'Good'].includes(status)
        ? 'good'
        : ['fail', 'failed', 'Bad', 'tests_failed', 'rejected'].includes(status)
          ? 'bad'
          : 'unknown'),
    statusNames[status] || status || '未记录',
  );
}

export function button(text, fn, cls = 'quiet') {
  const b = el('button', cls, text);
  b.type = 'button';
  b.addEventListener('click', () => Promise.resolve(fn()).catch(notice));
  return b;
}

export function notice(error) {
  const n = $('#notice');
  n.textContent = error.message || String(error);
  n.hidden = false;
}

export function hero(title, desc, kicker = '方法与实现') {
  const h = el('section', 'hero');
  const copy = el('div');
  copy.append(el('div', 'eyebrow', kicker), el('h1', '', title), el('p', '', desc));
  h.append(copy, el('div', 'stamp', '教育智能体研究原型'));
  return h;
}

export function panel(title) {
  const p = el('section', 'panel');
  if (title) p.append(el('h2', '', title));
  return p;
}

export function empty(text) {
  return el('p', 'empty', text);
}

export function openDetail(title, nodes) {
  $('#detail-title').textContent = title;
  $('#detail-body').replaceChildren(...nodes);
  if (!$('#detail').open) $('#detail').showModal();
}

export function rows(items, action) {
  const box = el('div');
  if (!items.length) return empty('当前选择下没有记录。可在任务工作台创建新任务。');
  for (const item of items) {
    const row = el('div', 'row');
    const copy = el('div');
    copy.append(
      button(item.label, () => action(item), 'text-link'),
      el('small', '', item.student_id || item.path),
    );
    row.append(copy, tag(item.status));
    box.append(row);
  }
  return box;
}

export function documentLink(section) {
  return el('p', 'document-link', '《目前进展》 · ' + section);
}
