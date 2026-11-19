import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {advise} from './advisor.js';
const load=async path=>JSON.parse((await readFile(new URL(path,import.meta.url),'utf8')).replace(/^\uFEFF/,''));
const data=Object.fromEntries(await Promise.all(['predictor','clusters','models','tiers','prices'].map(async key=>[key,await load(`./data/${key}.json`)])));
const cases=await load('./fixtures.json');
assert.equal(cases.length,240);
for (const [i,c] of cases.entries()) {
 const a=advise(c.text,c.stakes,c.engine,data),where=`case ${i}: ${c.engine}/${c.stakes}`;
 assert.deepEqual(a.features,c.features,`${where}: all fast text features`);
 assert.equal(a.category,c.features.category,where);
 assert.equal(a.stakes,c.stakes,where);
 for(const k of ['id','effort','why'])assert.equal(a.recommendation[k],c.recommendation[k],`${where}: ${k}`);
 assert.ok(Math.abs(a.recommendation.predicted-c.recommendation.predicted)<1e-6,where);
 for(let t=0;t<3;t++)assert.ok(Math.abs(a.tiers[t].predicted_pass_chance-c.core.tiers[t].predicted_pass_chance)<1e-6,`${where}: tier ${t}`);
 assert.deepEqual(a.tiers,c.core.tiers,`${where}: full tier details`);
 for(const k of ['complexity','agent_setup','verification','confidence'])assert.deepEqual(a[k],c.core[k],`${where}: ${k}`);
}
console.log(`PASS: ${cases.length} cases, all features, tiers, choices and advice match Python.`);
