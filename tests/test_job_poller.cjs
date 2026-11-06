const test = require('node:test');
const assert = require('node:assert/strict');
const {pollJob} = require('../webapp/static/job-poller.js');
test('re-entering a page can follow an already running task to completion', async()=>{
  const snapshots=[{status:'running'},{status:'succeeded'}], seen=[];
  await pollJob('existing',async()=>snapshots.shift(),j=>seen.push(j.status),()=>true,async()=>{});
  assert.deepEqual(seen,['running','succeeded']);
});
test('leaving a page stops callbacks and further requests',async()=>{
  let current=true,calls=0;const seen=[];
  await pollJob('old',async()=>{calls++;return {status:'running'};},j=>seen.push(j.status),()=>current,async()=>{current=false;});
  assert.equal(calls,1);assert.deepEqual(seen,['running']);
});
test('response arriving after selection changes cannot update the new page',async()=>{
  let current=true,called=false;
  await pollJob('old',async()=>{current=false;return {status:'succeeded'};},()=>called=true,()=>current,async()=>{});
  assert.equal(called,false);
});
