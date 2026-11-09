import { state } from './state.js';
export async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.method) {
    headers['Content-Type'] = 'application/json';
    headers['X-Workbench-Token'] = state.data.csrf_token;
  }
  const r = await fetch(path, { ...options, headers });
  let data;
  try {
    data = await r.json();
  } catch {
    throw Error('服务器未返回有效数据。请检查本地服务。');
  }
  if (!r.ok) throw Error(data.error || `请求失败（${r.status}）`);
  return data;
}

export async function artifact(path) {
  if (!state.artifacts.has(path)) {
    const d = await api('/api/artifact?path=' + encodeURIComponent(path));
    state.artifacts.set(path, d.artifact);
  }
  return state.artifacts.get(path);
}
