(function(root){
  'use strict';
  async function pollJob(id,fetchJob,onJob,isCurrent,delay){
    while(isCurrent()){
      const job=await fetchJob(id);
      if(!isCurrent())return;
      onJob(job);
      if(!['queued','running'].includes(job.status))return;
      await delay();
    }
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={pollJob};
  else root.WorkbenchPolling={pollJob};
})(globalThis);
