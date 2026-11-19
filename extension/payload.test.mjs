import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {advise} from './advisor.js';
import {buildEvent,FEATURE_KEYS} from './payload.js';
const data=Object.fromEntries(await Promise.all(['predictor','clusters','models','tiers','prices'].map(async k=>[k,JSON.parse(await readFile(new URL(`data/${k}.json`,import.meta.url),'utf8'))])));
for(let i=0;i<20;i++){
 const secret=`private_marker_${i}_cannot_leave`;
 const prompt=`Write code to process ${secret}. Must use tests. https://private.example/${secret}`;
 const a=advise(prompt,'medium',i%2?'claude':'codex',data);
 const event=buildEvent({...a.features,prompt,text:prompt,base_predictions:{...a.features.base_predictions,text:prompt}}, {...a.recommendation,prompt},prompt,1,['v']);
 assert.deepEqual(Object.keys(event).sort(),['features','recommendation','siteModel','rating','tags','engine','source','client'].sort());
 assert.deepEqual(Object.keys(event.features).sort(),[...FEATURE_KEYS].sort());
 assert.deepEqual(Object.keys(event.recommendation).sort(),['effort','id','predicted']);
 for(const part of [prompt,secret,'private.example'])assert.ok(!JSON.stringify(event).includes(part));
 assert.equal(event.siteModel,'unknown');assert.equal(event.engine,i%2?'claude':'chatgpt');
 const poisoned=buildEvent(Object.fromEntries(FEATURE_KEYS.map(k=>[k,prompt])),a.recommendation,prompt,0,[]);
 assert.ok(!JSON.stringify(poisoned).includes(secret));
 assert.throws(()=>buildEvent(a.features,a.recommendation,'unknown',1,['bad']));
}
console.log('20 prompt privacy, schema, engine mapping and invalid tag checks passed.');
