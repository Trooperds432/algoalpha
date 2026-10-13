import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
const root='C:/Users/Jonat/algoalpha';
const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
const read=p=>JSON.parse(fs.readFileSync(root+'/'+p,'utf8').replace(/^\uFEFF/,''));
const company=read('config/company.json'), team=read('config/team-plan.json'), base=read('config/adapter-config.json');
function call(args){const p=spawnSync(process.execPath,[cli,...args,'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:16*1024*1024});if(p.status!==0)throw new Error(p.stderr||p.stdout);return JSON.parse(p.stdout);}
const existing=call(['agent','list','--company-id',company.id]);
const ids={};for(const r of team){const found=existing.filter(a=>a.name===r.name);if(found.length>1)throw new Error('Duplicate role: '+r.name);if(found.length)ids[r.slug]=found[0].id;}
const ordered=[];const pending=[...team];while(pending.length){const index=pending.findIndex(r=>r.reports_to==='board'||ordered.some(m=>m.slug===r.reports_to));if(index<0)throw new Error('Reporting cycle');ordered.push(pending.splice(index,1)[0]);}for(const r of ordered){if(ids[r.slug])continue;if(r.reports_to!=='board'&&!ids[r.reports_to])throw new Error('Missing manager for '+r.slug);
const payload={name:r.name,title:r.name,role:r.slug==='ceo'?'ceo':'general',reportsTo:r.reports_to==='board'?null:ids[r.reports_to],capabilities:r.mandate,adapterType:'codex_local',adapterConfig:{...base,instructionsFilePath:root+'/agents/'+r.slug+'/AGENTS.md'},permissions:{canCreateAgents:false},metadata:{firmRoleSlug:r.slug,paperTradingOnly:true}};
const a=call(['agent','create','--company-id',company.id,'--payload-json',JSON.stringify(payload)]);ids[r.slug]=a.id;fs.writeFileSync(root+'/config/agent-ids.json',JSON.stringify(ids,null,2));console.log(r.slug+' '+a.id);}
fs.writeFileSync(root+'/config/agent-ids.txt',Object.entries(ids).map(([k,v])=>k+': '+v).join('\n')+'\n');

