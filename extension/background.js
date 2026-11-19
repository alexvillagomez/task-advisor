// Serialize all queue mutations. A failed POST remains queued, at most 500 events.
let work=Promise.resolve();
async function save(payload){
 const stored=await chrome.storage.local.get('queue');
 let queue=stored.queue||[];
 if(payload)queue=[...queue,payload].slice(-500);
 await chrome.storage.local.set({queue});
 if(!queue.length){
  try{
   const response=await fetch('http://127.0.0.1:8765/api/ext/event',{method:'OPTIONS',signal:AbortSignal.timeout(5000)});
   return {saving:response.ok,queued:0};
  }catch{return {saving:false,queued:0};}
 }
 while(queue.length){
  try{
   const response=await fetch('http://127.0.0.1:8765/api/ext/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(queue[0]),signal:AbortSignal.timeout(5000)});
   if(!response.ok)break;
  }catch{break;}
  queue.shift();await chrome.storage.local.set({queue});
 }
 return {saving:queue.length===0,queued:queue.length};
}
function flush(payload){const next=work.then(()=>save(payload));work=next.catch(()=>{});return next;}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if(sender.id!==chrome.runtime.id||!sender.tab||!/^https:\/\/(chatgpt\.com|claude\.ai)\//.test(sender.tab.url||'')||!['event','status'].includes(message.type))return;
 flush(message.type==='event'?message.payload:null).then(respond,()=>respond({saving:false}));return true;
});
// Open content scripts ping every 15 seconds, waking suspended MV3 workers.
chrome.runtime.onStartup.addListener(()=>{void flush();});
chrome.runtime.onInstalled.addListener(()=>{void flush();});
