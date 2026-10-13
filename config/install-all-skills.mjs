import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const root='C:/Users/Jonat/algoalpha',company='3b1d401d-7010-4eed-acd1-be7d52372c07';
const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
function call(args){const p=spawnSync(process.execPath,[cli,...args,'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:32*1024*1024});if(p.status!==0)throw new Error(p.stderr||p.stdout);return JSON.parse(p.stdout);}
const catalogue=call(['skills','browse']);
const installs=[];
for(const entry of catalogue){try{const result=call(['skills','install',entry.key,'--company-id',company]);installs.push({key:entry.key,action:result.action,warnings:result.warnings});console.log(entry.slug+': '+result.action);}catch(error){installs.push({key:entry.key,error:error.message});console.log(entry.slug+': ERROR '+error.message);}}
fs.writeFileSync(root+'/logs/agent-activity/all-skills-install.json',JSON.stringify({at:new Date().toISOString(),installs},null,2));
const skills=call(['skills','list','--company-id',company]);
fs.writeFileSync(root+'/config/skills-after.json',JSON.stringify(skills,null,2));
console.log('Library shape: '+(Array.isArray(skills)?'array '+skills.length:Object.keys(skills).join(',')));
