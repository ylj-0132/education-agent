// Session state shared by the existing pages.
export const state = {
  data: null,
  page: 'overview',
  student: '',
  operation: 'recommend',
  job: null,
  preview: null,
  artifacts: new Map(),
  epoch: 0,
};

export function filtered(items) {
  return (items || []).filter((x) => !state.student || x.student_id === state.student);
}
