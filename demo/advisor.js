// Pure text-only baseline router. No DOM, storage, provider calls or personal weights.
import {projection} from './projection.js';
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const sigmoid=x=>1/(1+Math.exp(-clamp(x,-24,24)));
const logit=x=>{x=clamp(x,.001,.999);return Math.log(x/(1-x));};
const count=(s,r)=>[...s.matchAll(r)].length;
const length=s=>[...s].length;
const words=s=>s.trim()?s.trim().split(/\s+/u):[];
const round=(x,n=0)=>{const v=x*10**n, f=Math.floor(v);return (v-f===.5?(f%2?f+1:f):Math.round(v))/10**n;};
const tiers=['cheap','mid','frontier'];
export function signals(text){
 const t=text.toLowerCase();
 return {code:/```|\b(code|function|class|api|bug|debug|refactor|python|javascript|typescript|sql|repository|test suite)\b/.test(t),
 external:/\b(latest|current|today|news|price|weather|browse|search|look up|website|documentation|cite|source)\b/.test(t),
 ambiguity:/\b(something|somehow|etc\.?|make it good|improve this|best way|you know)\b/.test(t),
 multi_step:count(t,/(?:^|\n)\s*(?:\d+[.)]|[-*])\s+|\b(?:then|after that|finally|first|second|multiple steps?)\b/g)>=2||count(t,/\band\b/g)>=3,
 long:words(text).length>180||length(text)>1400,numbers:count(text,/\b\d+(?:\.\d+)?\b/g),
 high_risk:/\b(medical|diagnos|patient|legal|lawsuit|contract|tax|invest|financial advice|safety critical|production database)\b/.test(t),
 word_count:words(text).length,line_count:count(text,/\n/g)+1};
}
function textFeatures(text){
 const tokens=(text.match(/[a-z][a-z0-9_+-]{1,}|\d+(?:\.\d+)?/gi)||[]).map(t=>/^\d/.test(t)?'<number>':t.toLowerCase());
 return [...new Set([...tokens.map(w=>'u:'+w),...tokens.slice(1).map((w,i)=>'b:'+tokens[i]+'_'+w)])];
}
export function coreAdvice(text,stakes,data){
 if(typeof text!=='string'||!text.trim())throw new Error('Enter a task.');
 stakes=stakes.toLowerCase();if(!['low','medium','high'].includes(stakes))throw new Error('Invalid stakes');
 const s=signals(text),a=data.predictor,present=new Set(textFeatures(text)),vocab=new Map(a.vocabulary.map((t,i)=>[t,i]));
 let family='',hits=0;
 for(const [name,signature] of Object.entries(a.family_signatures)){
  const h=signature.filter(t=>present.has(t)).length;
  if(h>hits||(h===hits&&name>family)){hits=h;family=name;}
 }
 let evidence=Math.min(1,hits/3),flag=evidence<.3?'low evidence':'in distribution';
 if(s.external||s.high_risk){flag='low evidence';evidence=Math.min(evidence,.2);}
 const penalty=(s.code?.45:0)+(s.external?.55:0)+(s.ambiguity?.5:0)+(s.multi_step?.45:0)+(s.long?.4:0)+Math.min(.35,s.numbers*.035);
 let previous=0;
 const results=tiers.map((tier,i)=>{
  const mapping=data.tiers.tiers[tier],m=a.models[mapping.learned_configuration],w=m.weights;
  let score=Number(m.intercept);
  let sum=0;for(const t of present)if(vocab.has(t))sum+=Number(w[vocab.get(t)]);score+=sum;
  score+=Number(w[w.length-2])*Math.log1p(words(text).length)/6;
  score+=Number(w[w.length-1])*Math.log1p(s.line_count)/3;
  let adjusted=sigmoid(logit(sigmoid(score))-penalty*[1,.72,.48][i]);
  if(flag==='low evidence')adjusted=.58*adjusted+.42*Number(m.base_rate)-.025;
  if(previous)adjusted=Math.max(adjusted,previous+.025);
  adjusted=clamp(adjusted,.03,.98);previous=adjusted;
  const rates=data.prices.models[mapping.pricing_model],input=Math.max(1,Math.ceil(length(text)/4)),output=Math.max(40,round(Number(m.mean_output_tokens)*clamp(.65+s.word_count/220,.55,2.4)));
  return {model_tier:tier,reasoning_effort:mapping.reasoning_effort,predicted_pass_chance:round(adjusted,3),rough_cost_usd:round((input*Number(rates.input)+output*Number(rates.output))/1e6,6)};
 });
 const recommendation=results.find(r=>r.predicted_pass_chance>={low:.72,medium:.88,high:.95}[stakes])||results[2];
 let complexity=round(100*(.72*(1-results[2].predicted_pass_chance)+.28*Math.min(1,penalty/2.1)));
 for(const [key,min] of [['code',36],['external',42],['multi_step',46],['long',50],['high_risk',55]])if(s[key])complexity=Math.max(complexity,min);
 complexity=clamp(complexity,0,100);
 const reasons=[];
 for(const [key,why] of [['code','code needs executable checks'],['external','needs current or external facts'],['multi_step','multiple dependent steps'],['ambiguity','requirements are underspecified'],['long','long context']])if(s[key])reasons.push(why);
 if(s.numbers>=3)reasons.push('numeric precision matters');if(s.high_risk)reasons.push('high-risk domain');
 reasons.push(complexity<35?'learned pass rate is high':'learned pass rate is limited');if(reasons.length<2)reasons.unshift('short, direct request');
 return {stakes,complexity:{score:complexity,label:complexity<20?'trivial':complexity<45?'routine':complexity<70?'hard':'frontier',reasons:reasons.slice(0,4)},tiers:results,recommendation,
 agent_setup:s.code?'coding agent with tests':s.external?'tools/retrieval':s.multi_step||complexity>=55?'decompose':'single call',
 verification:verification(stakes,recommendation.predicted_pass_chance,s),confidence:{flag,evidence_score:round(evidence,3),nearest_training_family:family||null}};
}
function verification(stakes,p,s){return s.high_risk&&stakes==='high'?'human review':s.code?'executable test':stakes==='high'||p<.7?'cross-model check':stakes==='medium'||p<.9?'self-check':'none';}
// BLAKE2b-64, matching hashlib.blake2b(digest_size=8), not a truncated 512-bit hash.
const mask=(1n<<64n)-1n;
const iv=[0x6a09e667f3bcc908n,0xbb67ae8584caa73bn,0x3c6ef372fe94f82bn,0xa54ff53a5f1d36f1n,0x510e527fade682d1n,0x9b05688c2b3e6c1fn,0x1f83d9abfb41bd6bn,0x5be0cd19137e2179n];
const permutations=[[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15],[14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3],[11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4],[7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8],[9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13],[2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9],[12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11],[13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10],[6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5],[10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0]];
export function hash64(word){
 const input=new TextEncoder().encode(word),h=[...iv];h[0]^=0x01010008n;
 for(let start=0;start<input.length||start===0;start+=128){
  const size=Math.min(128,input.length-start),m=Array(16).fill(0n);
  for(let j=0;j<size;j++)m[j>>3]|=BigInt(input[start+j])<<BigInt(8*(j%8));
  const v=[...h,...iv];v[12]^=BigInt(start+size);if(start+size===input.length)v[14]^=mask;
  const rot=(x,n)=>((x>>n)|(x<<(64n-n)))&mask;
  function g(a,b,c,d,x,y){v[a]=(v[a]+v[b]+x)&mask;v[d]=rot(v[d]^v[a],32n);v[c]=(v[c]+v[d])&mask;v[b]=rot(v[b]^v[c],24n);v[a]=(v[a]+v[b]+y)&mask;v[d]=rot(v[d]^v[a],16n);v[c]=(v[c]+v[d])&mask;v[b]=rot(v[b]^v[c],63n);}
  for(let r=0;r<12;r++){const s=permutations[r%10];g(0,4,8,12,m[s[0]],m[s[1]]);g(1,5,9,13,m[s[2]],m[s[3]]);g(2,6,10,14,m[s[4]],m[s[5]]);g(3,7,11,15,m[s[6]],m[s[7]]);g(0,5,10,15,m[s[8]],m[s[9]]);g(1,6,11,12,m[s[10]],m[s[11]]);g(2,7,8,13,m[s[12]],m[s[13]]);g(3,4,9,14,m[s[14]],m[s[15]]);}
  for(let j=0;j<8;j++)h[j]^=v[j]^v[j+8];
 }
 let big=0n;for(let i=0;i<8;i++)big=(big<<8n)|((h[0]>>BigInt(8*i))&255n);return big;
}
const wordMatches=text=>text.match(/[\p{L}\p{N}_]+/gu)||[];
function cluster(text,data){
 const bag=Array(256).fill(0);
 for(const word of wordMatches(text.toLowerCase())){const h=hash64(word);bag[Number(h%256n)]+=((h>>8n)&1n)?1:-1;}
 let v=Array.from({length:32},(_,j)=>bag.reduce((sum,b,i)=>sum+b*projection[i][j],0));
 const norm=Math.sqrt(v.reduce((sum,x)=>sum+x*x,0))||1;v=v.map(x=>x/norm);
 let best=0,score=-Infinity;data.clusters.centroids.forEach((c,i)=>{const s=v.reduce((sum,a,j)=>sum+a*c[j],0);if(s>score){best=i;score=s;}});return best;
}
export function featurize(text,stakes,data){
 const a=coreAdvice(text||' — ',stakes,data),s=signals(text),math=/\b(calculate|math|equation|sum|multiply|probability)\b|\d\s*[+*/=]\s*\d/i.test(text),ws=wordMatches(text);
 const f={length_bucket:length(text)<240?'short':length(text)<1400?'medium':'long',code:s.code,math,multistep:s.multi_step,question:text.includes('?'),category:s.code?'code':math?'math':s.external?'research':/\b(write|rewrite|summarize|draft|translate)\b/i.test(text)?'writing':'general',stakes,
 base_predictions:Object.fromEntries(a.tiers.map(t=>[t.model_tier,t.predicted_pass_chance])),turn_index:0};
 const nullable='turns_since_switch cumulative_tokens local_hour weekday dirty_files seconds_since_prompt session_age is_git has_tests has_ci weekend previous_effort file_count_bucket project_size_bucket previous_model previous_label top_extensions'.split(' ');
 for(const k of nullable)f[k]=null;
 Object.assign(f,{chars:length(text),words:ws.length,lines:count(text,/\n/g)+1,code_blocks:Math.floor(count(text,/```/g)/2),inline_code:count(text,/(?<!`)`[^`\n]+`(?!`)/g),file_paths:count(text,/(?:[\p{L}\p{N}_.-]+[/\\])+[\p{L}\p{N}_.-]+|\b\w+\.(?:py|js|ts|json|md|txt|csv)\b/gu),urls:count(text,/https?:\/\/\S+/g),numbers:s.numbers,list_items:count(text,/^\s*(?:[-*+]\s|\d+[.)]\s)/gm),question_marks:count(text,/\?/g),imperative_start:/^\s*(?:please\s+)?(?:write|create|make|fix|build|read|show|explain|find|list|calculate|summarize|translate|compare|implement|run)\b/i.test(text),language:!ws.length||ws.filter(w=>/^[\x00-\x7f]*$/.test(w)).length/ws.length>=.8?'en':'other',constraints:count(text,/\b(?:must|exactly|only)\b/gi),output_format:['json','table','code'].find(k=>new RegExp('\\b'+k+'\\b','i').test(text))||'none',previous_reference:/\b(?:that|above|again)\b/i.test(text),correction_cue:/\b(?:wrong|incorrect|instead|correction|not what|try again)\b/i.test(text),cluster_id:cluster(text,data),cluster_backend:'hash'});
 return f;
}
export function advise(text,stakes='medium',engine='codex',data){
 const core=coreAdvice(text,stakes,data),features=featurize(text,stakes,data),target={low:.72,medium:.88,high:.95}[stakes];
 const ranked=data.models.models.filter(m=>m.engine===engine).flatMap(m=>m.efforts.map(effort=>{
  const predicted=sigmoid(logit(features.base_predictions[m.tier])+({none:-.08,low:0,medium:.12,high:.24,xhigh:.3,max:.35}[effort]));
  const cost=({short:60,medium:350,long:1500}[features.length_bucket]*m.input+({low:300,medium:600,high:1200,xhigh:1800,max:2400}[effort]||300)*m.output)/1e6;
  // Default quota.price latency prior; threshold objective includes lambda_lat=0.00001.
  return {...m,effort,key:m.engine+':'+m.id+':'+effort,predicted,cost,latency:({low:5,medium:15,high:40}[effort]||60)};
 }));
 if(!ranked.length)throw new Error('Invalid engine');
 const eligible=ranked.filter(o=>o.predicted>=target),order={none:0,low:1,medium:2,high:3,xhigh:4,max:5};
 // Stable sorting preserves Python's first-item tie behavior.
 const chosen=eligible.length?eligible.sort((a,b)=>(a.cost+.00001*a.latency)-(b.cost+.00001*b.latency)||order[a.effort]-order[b.effort]||b.predicted-a.predicted)[0]:ranked.sort((a,b)=>b.predicted-a.predicted||a.cost-b.cost)[0];
 return {...core,category:features.category,features,recommendation:{...chosen,target,why:eligible.length?'cheapest above target':'best predicted; target unmet'}};
}
