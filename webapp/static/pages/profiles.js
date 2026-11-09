import { state, filtered } from '../state.js';
import { el, tag, button, hero, panel, empty, openDetail, $ } from '../ui.js';
import { artifact } from '../api.js';
import { render } from '../router.js';
export async function profiles(content, epoch) {
  content.append(
    hero(
      '学生画像与证据',
      '知识点状态来自具体作答的模型推断。Good / Bad / Unknown 描述局部表现，不是掌握概率。',
      '结果浏览',
    ),
  );
  const list = filtered(state.data.profiles);
  if (!list.length) {
    content.append(empty('没有可展示的画像。请先建立画像，或切换学生。'));
    return;
  }
  const choices = el('div', 'choices');
  const chosen = list.find((x) => x.path === state.selectedProfile) || list[list.length - 1];
  state.selectedProfile = chosen.path;
  for (const item of list)
    choices.append(
      button(
        item.label,
        () => {
          state.selectedProfile = item.path;
          return render();
        },
        item.path === chosen.path ? 'active' : '',
      ),
    );
  content.append(choices);
  const p = await artifact(chosen.path);
  if (epoch !== state.epoch) return;
  const grid = el('div', 'grid'),
    left = panel('知识点表现'),
    right = panel('画像来源');
  right.append(
    tag(p.status),
    el('p', '', `学生：${p.student_id}`),
    el('p', '', `证据 ${p.evidence?.length || 0} 份 / 观察 ${p.observations?.length || 0} 条`),
    el('p', 'small muted', `实际模型：${p.model_id || '未记录'}`),
    el('p', 'note', '旧画像按原模型和原判断保留。当前模型配置不会自动修正旧推断。'),
  );
  const groups = {};
  for (const o of p.observations || []) (groups[o.concept_id] ??= []).push(o);
  for (const [id, obs] of Object.entries(groups)) {
    const c = el('article', 'concept');
    c.append(el('h3', '', p.concepts?.[id] || id), el('span', 'mono muted small', id));
    const counts = {};
    obs.forEach((o) => (counts[o.status] = (counts[o.status] || 0) + 1));
    const badges = el('div', 'choices');
    for (const [s, n] of Object.entries(counts)) {
      const t = tag(s);
      t.append(' × ' + n);
      badges.append(t);
    }
    c.append(
      badges,
      el('p', '', p.summaries?.[id]?.evaluation || '未提供此知识点汇总；以下保留逐条观察。'),
    );
    c.append(
      button(
        '查看 ' + obs.length + ' 条判断与原始证据 ↗',
        () => {
          const nodes = [];
          for (const o of obs) {
            const item = panel();
            item.append(
              tag(o.status),
              el('p', '', o.description),
              el('p', 'small muted', `模型推断 · ${o.model_id || p.model_id}`),
            );
            const e = p.evidence.find((v) => v.record_id === o.evidence_id);
            if (e) {
              item.append(
                el('h3', '', `原始作答 · ${e.problem_id}`),
                el('p', 'question-body', e.question),
                el('pre', '', e.program),
                el('p', 'mono small muted', e.record_id),
              );
              if (e.execution_verification)
                item.append(
                  el('p', 'small', '另有绑定运行观测：' + e.execution_verification.run_path),
                );
            } else item.append(empty('证据引用未找到，请检查此画像。'));
            nodes.push(item);
          }
          openDetail(p.concepts?.[id] || id, nodes);
        },
        'evidence',
      ),
    );
    left.append(c);
  }
  if (!Object.keys(groups).length) left.append(empty('没有已观察知识点，不能据此判定学生不会。'));
  grid.append(left, right);
  content.append(grid);
}
