import { state } from './state.js';
import { names } from './labels.js';
import { api } from './api.js';
import { $, el, button, notice } from './ui.js';
let renderers;

export async function load() {
  const data = await api('/api/bootstrap');
  state.data = data;
  state.artifacts.clear();
  const ids = [
    ...new Set(
      [...data.profiles, ...data.generations, ...data.recommendations]
        .map((v) => v.student_id)
        .filter(Boolean),
    ),
  ].sort();
  const select = $('#student');
  select.replaceChildren(new Option('全部学生', ''), ...ids.map((x) => new Option(x, x)));
  if (ids.includes(state.student)) select.value = state.student;
  else state.student = '';
  await render();
}
export async function render() {
  const epoch = ++state.epoch;
  $('#notice').hidden = true;
  $('#breadcrumb').textContent = '工作空间 / ' + names[state.page];
  document
    .querySelectorAll('nav button')
    .forEach((b) => b.classList.toggle('active', b.dataset.page === state.page));
  const content = $('#content');
  content.replaceChildren();
  if (!state.data) return;
  try {
    $('#student').disabled = ['demo', 'overview', 'skills'].includes(state.page);
    $('.header-actions label').hidden = $('#student').disabled;
    $('#student').hidden = $('#student').disabled;
    await renderers[state.page](content, epoch);
  } catch (e) {
    if (epoch === state.epoch) notice(e);
  }
}
export function navigate(page) {
  if ($('#detail').open) $('#detail').close();
  state.page = page;
  location.hash = page;
  return render();
}

export function presentationPath(active) {
  const nav = el('nav', 'presentation-path');
  nav.setAttribute('aria-label', '讲解顺序');
  [
    ['overview', '01', '系统方法'],
    ['demo', '02', '实际案例'],
    ['skills', '03', '技能进化实验'],
  ].forEach(([page, no, title]) => {
    const b = button(`${no}  ${title}`, () => navigate(page), page === active ? 'selected' : '');
    if (page === active) b.setAttribute('aria-current', 'page');
    nav.append(b);
  });
  return nav;
}
export function start(pages) {
  renderers = pages;
  document
    .querySelectorAll('nav button')
    .forEach((b) => b.addEventListener('click', () => navigate(b.dataset.page)));
  $('#student').addEventListener('change', () => {
    state.student = $('#student').value;
    if ($('#detail').open) $('#detail').close();
    state.preview = null;
    render();
  });
  $('#refresh').addEventListener('click', () => load().catch(notice));
  $('#close-detail').addEventListener('click', () => $('#detail').close());
  if (names[location.hash.slice(1)]) state.page = location.hash.slice(1);
  load().catch(notice);
}
