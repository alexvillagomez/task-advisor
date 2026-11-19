import {copyFile, mkdir, readFile, writeFile, readdir} from 'node:fs/promises';
const here=new URL('./',import.meta.url), demo=new URL('../demo/',here);
await mkdir(new URL('data/',here),{recursive:true});
for(const name of ['advisor.js','projection.js'])await copyFile(new URL(name,demo),new URL(name,here));
for(const name of await readdir(new URL('data/',demo)))if(name.endsWith('.json'))await copyFile(new URL('data/'+name,demo),new URL('data/'+name,here));
const projection=await readFile(new URL('projection.js',here),'utf8');
const advisor=await readFile(new URL('advisor.js',here),'utf8');
// Classic isolated-world content scripts: no page-injected code or exposed resources.
await writeFile(new URL('advisor.bundle.js',here),'(()=>{\n'+projection.replace(/export /g,'')+'\n'+advisor.replace(/^import .*;\r?\n/m,'').replace(/export /g,'')+'\nglobalThis.TaskAdvisor={advise};})();\n');
const payload=await readFile(new URL('payload.js',here),'utf8');
const data=Object.fromEntries(await Promise.all(['predictor','clusters','models','tiers','prices'].map(async k=>[k,JSON.parse(await readFile(new URL('data/'+k+'.json',here),'utf8'))])));
await writeFile(new URL('advisor.bundle.js',here),'\nglobalThis.TaskAdvisor.data='+JSON.stringify(data)+';\n',{flag:'a'});
await writeFile(new URL('payload.bundle.js',here),'(()=>{\n'+payload.replace(/export /g,'')+'\nglobalThis.TaskAdvisorPayload={buildEvent};})();\n');
console.log('Synced local predictor, projection, data and content-script bundles.');
