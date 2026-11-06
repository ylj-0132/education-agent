const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const ctx={};
const file=require('node:path').join(__dirname,'../webapp/static/demo-route.js');
if(fs.existsSync(file))vm.runInNewContext(fs.readFileSync(file,'utf8'),ctx);
function sample(){return [{student_id:'s',evidence:[{record_id:'e'}]}, {student_id:'s',status:'recommended',recommendation_id:'r',decision:{evidence_ids:['e']}}, {student_id:'s',recommendation_id:'r',versions:[{question:{}}]}];}
test('saved route validates linked results and referenced evidence',()=>{assert.ok(ctx.SavedDemo,'SavedDemo module must exist');assert.equal(ctx.SavedDemo.validate(...sample(),'s').length,1);});
test('rejects another student, another recommendation, and missing evidence',()=>{assert.ok(ctx.SavedDemo);for(const kind of ['student','recommendation','evidence']){const [p,r,g]=sample();if(kind==='student')g.student_id='other';if(kind==='recommendation')g.recommendation_id='other';if(kind==='evidence')p.evidence=[];assert.throws(()=>ctx.SavedDemo.validate(p,r,g,'s'));}});
