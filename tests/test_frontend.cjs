const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');

const modules = Promise.all([
  import('../webapp/static/api.js'),
  import('../webapp/static/state.js'),
  import('../webapp/static/pages/tasks.js'),
]);
const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

test('result reads are cached and never send a task request', async () => {
  const [{ artifact }, { state }] = await modules;
  state.artifacts.clear();
  const requests = [];
  global.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ artifact: { student_id: 's1' } }) };
  };
  assert.deepEqual(await artifact('runs/中文/profile.json'), { student_id: 's1' });
  await artifact('runs/中文/profile.json');
  assert.equal(requests.length, 1);
  assert.equal(
    requests[0].url,
    '/api/artifact?path=' + encodeURIComponent('runs/中文/profile.json'),
  );
  assert.equal(requests[0].options.method, undefined);
  assert.equal(requests[0].options.headers['X-Workbench-Token'], undefined);
});

test('explicit writes use the current token and surface server rejection', async () => {
  const [{ api }, { state }] = await modules;
  state.data = { csrf_token: 'test-token' };
  let request;
  global.fetch = async (url, options) => {
    request = { url, options };
    return { ok: false, status: 409, json: async () => ({ error: 'Another task is running' }) };
  };
  await assert.rejects(api('/api/jobs', { method: 'POST', body: '{}' }), /Another task is running/);
  assert.equal(request.options.headers['X-Workbench-Token'], 'test-token');
  assert.equal(request.options.headers['Content-Type'], 'application/json');
  assert.equal(request.options.body, '{}');
});

test('invalid JSON response produces a readable error', async () => {
  const [{ api }] = await modules;
  global.fetch = async () => ({
    ok: true,
    json: async () => {
      throw Error('bad JSON');
    },
  });
  await assert.rejects(api('/api/bootstrap'), /服务器未返回有效数据/);
});

test('form fields retain lists and omit confirmation and empty inputs', async () => {
  const [, , { fieldsFrom }] = await modules;
  const fields = fieldsFrom({
    elements: [
      { name: 'student', value: ' s1 ', dataset: {} },
      {
        name: 'requirement',
        value: ' 第一条\n\n--execute\n',
        dataset: { multiple: 'true' },
        tagName: 'TEXTAREA',
      },
      {
        name: 'scope',
        dataset: { multiple: 'true' },
        tagName: 'SELECT',
        selectedOptions: [{ value: 'java.array' }],
      },
      { name: 'confirm', value: 'on', dataset: {} },
      { name: 'cases', value: ' ', dataset: {} },
    ],
  });
  assert.deepEqual(fields, {
    student: 's1',
    requirement: ['第一条', '--execute'],
    scope: ['java.array'],
  });
});
