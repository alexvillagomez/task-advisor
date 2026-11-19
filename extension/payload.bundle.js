(()=>{
﻿// Explicit schema boundary. Never spread caller objects into feedback.
const FEATURE_KEYS=('length_bucket code math multistep question category stakes base_predictions turn_index chars words lines code_blocks inline_code file_paths urls numbers list_items question_marks constraints cluster_id turns_since_switch cumulative_tokens local_hour weekday dirty_files seconds_since_prompt session_age imperative_start previous_reference correction_cue is_git has_tests has_ci weekend language output_format cluster_backend previous_effort file_count_bucket project_size_bucket previous_model previous_label top_extensions').split(' ');
const enums={length_bucket:['short','medium','long'],category:['code','math','research','writing','general'],stakes:['low','medium','high'],language:['en','other'],output_format:['none','json','table','code'],cluster_backend:['hash','minilm'],previous_effort:['none','low','medium','high','xhigh','max','unknown'],file_count_bucket:['tiny','small','medium','large'],project_size_bucket:['tiny','small','medium','large']};
const modelIds=['gpt-6-luna','gpt-6.1-terra','gpt-6.1-sol','gpt-6-astra','haiku','sonnet','opus','unknown'];
const extensions=['.py','.js','.ts','.tsx','.jsx','.go','.rs','.java','.cs','.cpp','.c','.rb','.php','.swift','.kt','.sql','.sh','.ps1','.html','.css','.r','.ipynb','other'];
function buildEvent(features,recommendation,siteModel,rating,tags){
 const engine={codex:'chatgpt',chatgpt:'chatgpt',claude:'claude','claude-web':'claude'}[recommendation.engine];
 if(!engine||![0,1].includes(rating)||!Array.isArray(tags)||tags.some(t=>!['v','o','u','w','p','s','c'].includes(t)))throw Error('Invalid feedback');
 const clean={};
 for(const key of FEATURE_KEYS){
  const value=features[key];
  if(key==='base_predictions'){clean[key]=Object.fromEntries(['cheap','mid','frontier'].map(t=>[t,typeof value?.[t]==='number'&&Number.isFinite(value[t])?Math.max(0,Math.min(1,value[t])):0]));continue;}
  if(key==='previous_model'){clean[key]=modelIds.includes(value)?value:null;continue;}
  if(key==='top_extensions'){clean[key]=Array.isArray(value)?value.filter(v=>extensions.includes(v)).slice(0,3):null;continue;}
  clean[key]=enums[key]?(enums[key].includes(value)?value:null):(typeof value==='boolean'||typeof value==='number'&&Number.isFinite(value)?value:null);
 }
 if(!modelIds.includes(recommendation.id)||recommendation.id==='unknown'||!['none','low','medium','high','xhigh','max'].includes(recommendation.effort)||!Number.isFinite(recommendation.predicted))throw Error('Invalid recommendation');
 return {features:clean,recommendation:{id:recommendation.id,effort:recommendation.effort,predicted:Math.max(0,Math.min(1,recommendation.predicted))},siteModel:modelIds.includes(siteModel)?siteModel:'unknown',rating,tags:[...new Set(tags)].sort(),engine,source:'live',client:'extension'};
}

globalThis.TaskAdvisorPayload={buildEvent};})();
