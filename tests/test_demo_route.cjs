const { test } = require('node:test');
const assert = require('node:assert/strict');
const savedDemo = import('../webapp/static/demo-route.js');
function sample() {
  return [
    { student_id: 's', evidence: [{ record_id: 'e' }] },
    {
      student_id: 's',
      status: 'recommended',
      recommendation_id: 'r',
      decision: { evidence_ids: ['e'] },
    },
    { student_id: 's', recommendation_id: 'r', versions: [{ question: {} }] },
  ];
}
test('saved route validates linked results and referenced evidence', async () => {
  const { SavedDemo } = await savedDemo;
  assert.equal(SavedDemo.validate(...sample(), 's').length, 1);
});
test('rejects another student, another recommendation, and missing evidence', async () => {
  const { SavedDemo } = await savedDemo;
  for (const kind of ['student', 'recommendation', 'evidence']) {
    const [p, r, g] = sample();
    if (kind === 'student') g.student_id = 'other';
    if (kind === 'recommendation') g.recommendation_id = 'other';
    if (kind === 'evidence') p.evidence = [];
    assert.throws(() => SavedDemo.validate(p, r, g, 's'));
  }
});
