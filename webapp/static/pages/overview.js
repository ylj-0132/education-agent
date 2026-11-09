import { state } from '../state.js';
import { el, button, hero, panel, documentLink } from '../ui.js';
import { navigate, presentationPath } from '../router.js';
export function overview(content) {
  content.append(
    hero(
      '教育智能体：方法与结果',
      '历史作答形成画像，画像支持目标推荐，目标驱动出题与审核。',
      '研究展示',
    ),
    presentationPath('overview'),
    documentLink('已经完成的系统工作；当前系统的总体方法'),
  );
  const stats = el('div', 'research-facts');
  for (const [value, label] of [
    ['250', '原始记录已入库'],
    ['200 / 50', '训练 / 测试划分'],
    ['3', '学生已有画像与推荐出题结果'],
  ]) {
    const item = el('div');
    item.append(el('strong', '', value), el('span', '', label));
    stats.append(item);
  }
  content.append(stats);
  const method = panel('系统如何完成一次练习安排');
  const flow = el('div', 'explain-flow');
  for (const [i, title, desc] of [
    ['01', '历史证据', '题干 + 学生代码'],
    ['02', '学生画像', '逐条分析 → 知识点汇总'],
    ['03', '练习目标', '一次推荐 → 程序校验'],
    ['04', '出题审核', '生成 → 六维审核 → 至多一次修订'],
  ]) {
    const cell = el('div');
    cell.append(el('span', 'flow-no', i), el('h3', '', title), el('p', '', desc));
    flow.append(cell);
  }
  method.append(flow);
  method.append(
    el('p', 'flow-return', '后续回流：接收真实作答 → 绑定题目版本 → 分析新证据 → 更新相关知识点'),
    el(
      'p',
      'small muted',
      '教师可直接指定目标。参考代码与学生代码分别支持经人工批准的 Java 验证。回流接口已实现，尚无真实学生使用新题后的教学效果数据。',
    ),
  );
  content.append(method);
  const grid = el('div', 'grid'),
    a = panel('画像与目标推荐'),
    b = panel('出题与审核');
  a.append(
    documentLink('历史数据到画像与目标推荐的执行流程'),
    el(
      'p',
      '',
      '每条表现关联原始作答，再按知识点汇总。推荐读取相关画像和知识关系，输出目标、理由与证据。',
    ),
    button(
      '查看作答、画像与推荐',
      () => {
        state.demoStep = 0;
        return navigate('demo');
      },
      'primary',
    ),
  );
  b.append(
    documentLink('出题审核与修订的实际控制流程'),
    el(
      'p',
      '',
      '目标与证据传入出题模型，形成题面、样例、参考代码和解析；六维审核定位问题，修订后再次审核。',
    ),
    button(
      '查看生成题与审核',
      () => {
        state.demoStep = 3;
        return navigate('demo');
      },
      'secondary',
    ),
  );
  grid.append(a, b);
  content.append(grid);
  const more = el('details', 'supporting-method');
  more.append(
    el('summary', '', '补充：作答回流与证据组织'),
    documentLink('作答接收运行验证与画像回流；系统方法的设计依据'),
    el(
      'p',
      '',
      '公共知识目录与个人状态通过知识点 ID 关联，不同学生证据隔离。收到作答后核验学生、题号和版本；运行结果与模型推断分别记录，新增证据只触发相关知识点汇总。',
    ),
    el(
      'p',
      'small muted',
      'Java_5 为首轮原型数据；全部数据条数不等于实际模型实验样本量。当前页面读取已有结果，不启动模型或 Java。',
    ),
  );
  content.append(more);
}
