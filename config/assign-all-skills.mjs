import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const root='C:/Users/Jonat/algoalpha',company='3b1d401d-7010-4eed-acd1-be7d52372c07';
const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
function call(args){const p=spawnSync(process.execPath,[cli,...args,'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024});if(p.status!==0)throw new Error(p.stderr||p.stdout);return JSON.parse(p.stdout);}
const skills=call(['skills','list','--company-id',company]);
const agents=call(['agent','list','--company-id',company]);
const results=[];
const keys=skills.map(s=>s.key);
for(const agent of agents){try{
const result=call(['skills','agent','sync',agent.id,'--company-id',company,'--mode','add',...keys.flatMap(key=>['--skill',key])]);
const snapshot=call(['skills','agent','list',agent.id,'--company-id',company]);
const desired=new Set(snapshot.desiredSkills);
const missing=keys.filter(k=>!desired.has(k));
const row={agentId:agent.id,name:agent.name,desiredCount:desired.size,missing,supported:snapshot.supported,mode:snapshot.mode,warnings:snapshot.warnings,states:[...new Set(snapshot.entries.filter(x=>x.desired).map(x=>x.state))]};
results.push(row);console.log(agent.name+': '+(missing.length?'MISSING '+missing.length:'verified '+keys.length)+'; '+row.states.join(','));
}catch(error){results.push({agentId:agent.id,name:agent.name,error:error.message});console.log(agent.name+': ERROR '+error.message);}
fs.writeFileSync(root+'/logs/agent-activity/all-agent-skills-verification.json',JSON.stringify({at:new Date().toISOString(),skillCount:skills.length,agentCount:agents.length,keys,results},null,2));
}
console.log('COMPLETE '+results.length+' agents; '+keys.length+' skills');
if(results.some(r=>r.error||r.missing?.length||!r.supported))process.exitCode=1;
