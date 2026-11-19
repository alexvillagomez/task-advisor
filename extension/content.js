(() => {
 const SITES={
  'chatgpt.com':{engine:'codex',composer:['#prompt-textarea','textarea[data-id="root"]','main [contenteditable="true"]','main textarea'],send:['button[data-testid="send-button"]','button[aria-label="Send prompt"]','button[aria-label="Send message"]'],stop:['button[data-testid="stop-button"]','button[aria-label="Stop generating"]'],model:['button[data-testid="model-switcher-dropdown-button"]','[data-testid="model-selector"]'],reply:['[data-message-author-role="assistant"]']},
  'claude.ai':{engine:'claude',composer:['[data-testid="chat-input"] [contenteditable="true"]','div[contenteditable="true"].ProseMirror','[contenteditable="true"][role="textbox"]','textarea'],send:['button[aria-label="Send message"]','button[data-testid="send-button"]'],stop:['button[aria-label="Stop response"]','button[aria-label="Stop generating"]','button[data-testid="stop-button"]'],model:['button[data-testid="model-selector-dropdown"]','[data-testid="model-selector"]','button[aria-label*="model" i]'],reply:['[data-is-streaming="true"]','[data-testid="assistant-message"]','.font-claude-response']}
 };
 const site=SITES[location.hostname];if(!site)return;
 const find=list=>list.map(s=>document.querySelector(s)).find(e=>e&&e.getClientRects().length);
 const host=document.createElement('div');host.id='task-advisor-extension';
 host.style.cssText='position:fixed;z-index:2147483646;max-width:calc(100vw - 16px);';
 const root=host.attachShadow({mode:'closed'});
 root.innerHTML=`<style>:host{color-scheme:light dark;font:12px system-ui}section{background:light-dark(#fff,#222);color:light-dark(#222,#eee);border:1px solid #888;border-radius:9px;padding:6px;box-shadow:0 2px 9px #0003;max-width:450px}button{font:inherit;color:inherit;background:transparent;border:1px solid #888;border-radius:5px;padding:4px;cursor:pointer}#chip{text-align:left;border:0}#details{white-space:pre-wrap;max-height:180px;overflow:auto;padding:6px}#note{font-size:11px}#rating{display:flex;flex-wrap:wrap;gap:4px;padding-top:5px}button[aria-pressed=true]{background:light-dark(#cde,#345)}[hidden]{display:none!important}</style><section><button id="chip" aria-expanded="false">advisor: ready</button><div id="details" hidden></div><div id="rating" hidden></div><div id="note"></div></section>`;
 document.documentElement.append(host);
 const chip=root.querySelector('#chip'),details=root.querySelector('#details'),ratingRow=root.querySelector('#rating'),note=root.querySelector('#note');
 let composer,latest,pending,rateable,timer,expanded=false,submitting=false;
 const completed=[];
 const text=()=>composer?.value??composer?.innerText??'';
 function siteModel(){
  const raw=find(site.model)?.textContent?.trim().toLowerCase()||'';
  return TaskAdvisor.data.models.models.find(m=>raw.includes(m.id.toLowerCase()))?.id||(['haiku','sonnet','opus'].find(id=>raw.includes(id)))||'unknown';
 }
 function position(){
  composer=find(site.composer);if(!composer){host.hidden=true;return;}
  host.hidden=false;const rect=composer.getBoundingClientRect();
  host.style.left=Math.max(8,Math.min(rect.left,innerWidth-host.offsetWidth-8))+'px';
  // Keep the whole panel above the composer and its send button.
  const top=rect.top-host.offsetHeight-12;
  host.style.top=Math.max(0,top)+'px';
  host.hidden=top<0; // Insufficient space: never overlay the send controls.
 }
 function render(a,model){
  if(!a)return;
  chip.textContent=`advisor: ${a.recommendation.id}/${a.recommendation.effort} would do (${Math.round(a.recommendation.predicted*100)}%) · verify: ${a.verification==='none'?'no':'yes'} · ${a.complexity.label}${model==='unknown'?'':` · you're on ${model}`}`;
  details.textContent=a.tiers.map(t=>`${t.model_tier}: ${Math.round(t.predicted_pass_chance*100)}%`).join(' · ')+`\nWhy: ${a.recommendation.why}; ${a.complexity.reasons.join('; ')}\nSetup: ${a.agent_setup}\nVerify: ${a.verification}`;
  position();
 }
 function predict(){
  composer=find(site.composer);const value=text();
  if(!value.trim()){latest=null;return;}
  latest={advice:TaskAdvisor.advise(value,'medium',site.engine,TaskAdvisor.data),model:siteModel()};
  if(!pending)render(latest.advice,latest.model);
 }
 chip.onclick=()=>{expanded=!expanded;details.hidden=!expanded;chip.setAttribute('aria-expanded',String(expanded));position();};
 function freeze(){
  if(pending)return;clearTimeout(timer);predict();if(!latest)return;
  pending={...latest,start:Date.now(),seen:false,initial:replySignature()};
  latest=null;render(pending.advice,pending.model);
 }
 function replySignature(){const nodes=site.reply.flatMap(s=>[...document.querySelectorAll(s)]);return nodes.length+':'+nodes.reduce((n,e)=>n+(e.textContent?.length||0),0);}
 function ready(){
  completed.push(pending);pending=null;
  if(!rateable&&!submitting)showRating();
 }
 function showRating(){
  rateable=completed.shift();ratingRow.replaceChildren();ratingRow.hidden=!rateable;
  if(!rateable){position();return;}
  const label=document.createElement('span');
  label.textContent=`Rate reply (${rateable.advice.recommendation.id}/${rateable.advice.recommendation.effort} advised)`;
  ratingRow.append(label);
  const tags=new Set();
  for(const [tag,title] of Object.entries({v:'verification needed',o:'overkill',u:'underpowered',w:'wrong',p:'partial',s:'slow',c:'costly'})){
   const b=document.createElement('button');b.textContent=tag;b.title=title;b.setAttribute('aria-label',title);b.setAttribute('aria-pressed','false');
   b.onclick=()=>{tags.has(tag)?tags.delete(tag):tags.add(tag);b.setAttribute('aria-pressed',String(tags.has(tag)));};ratingRow.append(b);
  }
  for(const [label,rating] of [['👍',1],['👎',0]]){
   const b=document.createElement('button');b.textContent=label;b.title=rating?'Good reply':'Bad reply';b.onclick=async()=>{
    if(!rateable||submitting)return;const turn=rateable;rateable=null;submitting=true;
    for(const button of ratingRow.querySelectorAll('button'))button.disabled=true;
    try{const payload=TaskAdvisorPayload.buildEvent(turn.advice.features,turn.advice.recommendation,turn.model,rating,[...tags]);showStatus(await chrome.runtime.sendMessage({type:'event',payload}));submitting=false;showRating();}
    catch{submitting=false;rateable=turn;for(const button of ratingRow.querySelectorAll('button'))button.disabled=false;showStatus({saving:false});}
    position();
   };ratingRow.prepend(b);
  }
  position();
 }
 function showStatus(status){note.textContent=status?.saving?'':'not saving — run `advisor serve`';position();}
 document.addEventListener('input',e=>{if(e.target===composer||composer?.contains(e.target)){clearTimeout(timer);timer=setTimeout(predict,400);}},true);
 document.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&!e.ctrlKey&&!e.altKey&&!e.metaKey&&(e.target===composer||composer?.contains(e.target)))freeze();},true);
 document.addEventListener('click',e=>{if(site.send.some(s=>e.target.closest?.(s))&&!e.target.closest('button')?.disabled)freeze();},true);
 document.addEventListener('submit',e=>{if(e.target.contains(composer))freeze();},true);
 setInterval(()=>{
  position();
  if(pending){
   const streaming=!!find(site.stop)||!!document.querySelector('[data-is-streaming="true"]');
   const signature=replySignature();
   if(streaming)pending.seen=true;
   if(signature!==pending.last){pending.last=signature;pending.changed=Date.now();}
   if((pending.seen&&!streaming)||(signature!==pending.initial&&!streaming&&Date.now()-pending.changed>1800)||Date.now()-pending.start>=120000)ready();
  }
 },600);
 async function status(){try{showStatus(await chrome.runtime.sendMessage({type:'status'}));}catch{showStatus({saving:false});}}
 setInterval(status,15000);void status();position();
})();
