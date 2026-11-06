'use strict';
globalThis.SavedDemo = {
  student: 'java_u344869639',
  paths: [
    'runs/java5-full-20261103/java_u344869639/profile.json',
    'runs/java5-demo-20261103/student1-recommendation/recommendation.json',
    'runs/java5-demo-20261103/student1-generation-fixed/generation.json'
  ],
  validate(profile, recommendation, generation, student) {
    if ([profile,recommendation,generation].some(x=>x.student_id!==student)) throw Error('演示记录的学生不一致。');
    if (recommendation.status!=='recommended' || !recommendation.recommendation_id || generation.recommendation_id!==recommendation.recommendation_id) throw Error('生成题与推荐记录不匹配。');
    if (!generation.versions?.at(-1)?.question) throw Error('生成任务没有可展示题目。');
    const ids=recommendation.decision?.evidence_ids||[];
    const evidence=ids.map(id=>profile.evidence?.find(e=>e.record_id===id));
    if (!ids.length || evidence.some(e=>!e)) throw Error('推荐引用的原始证据不完整。');
    return evidence;
  }
};
