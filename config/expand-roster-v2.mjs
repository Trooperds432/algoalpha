import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
const finalizeOnly=process.argv.includes('--finalize');
const root='C:/Users/Jonat/algoalpha', company='3b1d401d-7010-4eed-acd1-be7d52372c07';
const source='C:/Users/Jonat/Downloads/Global_Asset_Management_Firm_Expanded_Job_Descriptions_UPDATED_V2.txt';
const raw=fs.readFileSync(source,'utf8').replace(/^\uFEFF/,'');
const matches=[...raw.matchAll(/^(\d+\.\d+) (.+)\r?$/gm)];
const roles=matches.map((m,i)=>({code:m[1],title:m[2].trim(),description:raw.slice(m.index,matches[i+1]?.index??raw.length).trim()}));
if(roles.length!==370||new Set(roles.map(r=>r.code)).size!==370)throw Error('Expected 370 unique role codes');
const mapping={ceo:'3.1','quantitative-trader':'8.1','quantitative-researcher':'5.1','machine-learning-researcher':'5.11','institutional-sales-trading':'33.1','fundamental-research-analyst':'6.1','trading-operations':'15.1','software-engineer':'12.2','infrastructure-engineer':'12.5','production-engineer':'27.1','machine-learning-engineer':'20.1','data-engineer':'7.2','network-engineer':'12.9','systems-engineer':'12.8','site-reliability-engineer':'12.6','cybersecurity-engineer':'12.10','data-scientist':'51.1','quantitative-analyst':'20.2','research-analyst':'38.1','fundamental-researcher':'21.1','strategy-product':'34.1','product-manager':'23.5','business-analyst':'37.1','operations-analyst':'15.10',finance:'3.7',accounting:'15.6',treasury:'15.5','risk-management':'10.1','operations-specialist':'15.3','trade-support':'30.3','human-resources':'18.2',recruiting:'36.1','legal-counsel':'14.7','compliance-officer':'14.1',communications:'14.8',hospitality:'36.3','office-management':'17.1','building-operations':'36.4','facilities-engineer':'27.5',backtest:'11.1',execution:'8.10','cost-optimizer':'13.5',validation:'11.2','portfolio-manager':'4.1',reporting:'15.9'};
if(new Set(Object.values(mapping)).size!==45)throw Error('Mapping collision');
const api=async(path,method='GET',body)=>{const res=await fetch('http://127.0.0.1:3100/api'+path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});if(!res.ok)throw Error(method+' '+path+' '+res.status+' '+(await res.text()).slice(0,300));return res.json();};
const existing=await api('/companies/'+company+'/agents');
const oldIds=JSON.parse(fs.readFileSync(root+'/config/agent-ids.json','utf8'));
const oldPlan=JSON.parse(fs.readFileSync(root+'/config/team-plan.json','utf8'));
const backup=root+'/logs/agent-activity/roster-v2-backup';
fs.mkdirSync(backup,{recursive:true});
if(!fs.existsSync(backup+'/agents.json')){
 fs.writeFileSync(backup+'/agents.json',JSON.stringify(existing,null,2));
 for(const f of ['agent-ids.json','team-plan.json'])fs.copyFileSync(root+'/config/'+f,backup+'/'+f);
 fs.cpSync(root+'/agents',backup+'/instructions',{recursive:true});
 fs.copyFileSync(source,backup+'/source.txt');
}
const originalIds=JSON.parse(fs.readFileSync(backup+'/agent-ids.json','utf8'));
const oldByCode=Object.fromEntries(Object.entries(mapping).map(([slug,code])=>[code,{slug,id:originalIds[slug]}]));
const departments={4:'3.2',5:'5.1',6:'21.1',7:'3.8',8:'23.1',9:'24.1',10:'3.5',11:'25.4',12:'3.4',13:'28.1',14:'3.6',15:'3.3',16:'18.1',17:'3.1',18:'3.1',19:'19.1',20:'3.4',21:'3.2',22:'3.8',23:'3.3',24:'23.1',25:'3.5',26:'25.4',27:'3.4',28:'3.4',29:'3.6',30:'3.3',31:'18.1',32:null,33:'18.4',34:'18.4',35:'18.5',36:'18.2',37:'3.7',38:'5.1',39:'19.1',40:'24.1',41:'3.5',42:'25.4',43:'28.1',44:'3.8',45:'24.1',46:'3.7',47:'18.1',48:null,49:'18.5',50:'5.1',51:'3.2',52:'5.1',53:'3.7',54:'28.1',55:'3.6'};
const overrides={'3.1':null,'3.2':'3.1','3.3':'3.1','3.4':'3.1','3.5':'3.1','3.6':'3.1','3.7':'3.1','3.8':'3.1','5.1':'3.2','19.1':'3.2','18.5':null,'55.1':'48.3','54.2':'17.1','52.1':'19.1','17.3':'3.5','17.2':'3.2','17.4':'3.2'};
const shared=fs.readFileSync(root+'/AGENTS.md','utf8');
const template=existing.find(a=>a.id===originalIds.ceo);
const skills=template.adapterConfig.paperclipSkillSync.desiredSkills;
for(const r of roles){
 const old=oldByCode[r.code];
 r.slug=old?.slug??('role-'+r.code.replace('.','-')+'-'+r.title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,''));
 r.id=existing.find(a=>a.metadata?.rosterV2?.code===r.code)?.id??old?.id;
 r.name=roles.filter(x=>x.title===r.title).length>1?r.title+' ['+r.code+']':r.title;
 r.managerCode=Object.hasOwn(overrides,r.code)?overrides[r.code]:departments[r.code.split('.')[0]]??null;
 if(r.managerCode===r.code)throw Error('Self-manager '+r.code);
 r.instructions=root+'/agents/'+r.slug+'/AGENTS.md';
}
for(const r of roles){let c=r;const seen=new Set();while(c?.managerCode){if(seen.has(c.code))throw Error('Hierarchy cycle');seen.add(c.code);c=roles.find(x=>x.code===c.managerCode);if(!c)throw Error('Unknown manager');}}
const audit={source,sourceSha256:crypto.createHash('sha256').update(raw).digest('hex'),startedAt:new Date().toISOString(),originalCount:45,targetCount:370,roles:[]};
if(!finalizeOnly){
for(const r of roles){
 const old=oldByCode[r.code];
 const supplemental=old&&['validation','risk-management','portfolio-manager','backtest'].includes(old.slug)?'\n## Preserved independent controls\n'+oldPlan.find(p=>p.slug===old.slug)?.mandate+'\n':'';
 const content='# '+r.name+'\n\nSource role: '+r.code+'\n\n'+shared+'\n## Role description supplied by owner\n\n'+r.description+'\n'+supplemental+'\n## Operating interpretation\nThese responsibilities describe an AI role, not professional accreditation or delegated financial authority. Execute within the runtime contract and explicit owner authorisations. Titles do not grant spending, external communication, live trading or security-testing permissions. Source reporting and committee interfaces remain applicable; the application stores one administrative manager.\n';
 fs.mkdirSync(root+'/agents/'+r.slug,{recursive:true});fs.writeFileSync(r.instructions,content);
 const current=existing.find(a=>a.id===r.id);
 const body={name:r.name,title:r.title,capabilities:r.description,adapterType:current?.adapterType??'codex_local',adapterConfig:{...(current?.adapterConfig??{cwd:root,engine:'auto',search:true,dangerouslyBypassApprovalsAndSandbox:false,paperclipSkillSync:{desiredSkills:skills}}),instructionsFilePath:r.instructions},metadata:{...(current?.metadata??{}),rosterV2:{code:r.code,sourceSha256:audit.sourceSha256,previousName:current?.metadata?.rosterV2?.previousName??current?.name??null}}};
 let result;
 if(r.id)result=await api('/agents/'+r.id,'PATCH',body);
 else result=await api('/companies/'+company+'/agents','POST',{...body,role:'general',desiredSkills:skills,budgetMonthlyCents:0});
 r.id=result.id;
 audit.roles.push({code:r.code,id:r.id,name:r.name,previousSlug:old?.slug??null,action:old?'updated':'added'});
 fs.writeFileSync(root+'/logs/agent-activity/roster-v2-progress.json',JSON.stringify(audit,null,2));
 if(audit.roles.length%25===0)console.log('Applied '+audit.roles.length+'/370');
}
for(const r of roles){const manager=roles.find(x=>x.code===r.managerCode);await api('/agents/'+r.id,'PATCH',{reportsTo:manager?.id??null});}
}else audit.roles=JSON.parse(fs.readFileSync(root+'/logs/agent-activity/roster-v2-progress.json','utf8')).roles;
const fresh=await api('/companies/'+company+'/agents');
if(fresh.length!==370)throw Error('Final count '+fresh.length);
for(const r of roles){const a=fresh.find(a=>a.id===r.id);if(a?.metadata?.rosterV2?.code!==r.code||a.name!==r.name||a.capabilities!==r.description||path.resolve(a.adapterConfig.instructionsFilePath)!==path.resolve(r.instructions)||a.reportsTo!==(roles.find(x=>x.code===r.managerCode)?.id??null))throw Error('Verification failed '+r.code);if(!fs.readFileSync(r.instructions,'utf8').includes(r.description))throw Error('Instructions mismatch');if(a.adapterConfig.paperclipSkillSync?.desiredSkills?.length!==skills.length)throw Error('Skill mismatch '+r.code);}
if(Object.values(originalIds).some(id=>!fresh.some(a=>a.id===id)))throw Error('Lost original identity');
fs.writeFileSync(root+'/config/team-plan.json',JSON.stringify(roles.map(r=>({slug:r.slug,name:r.name,reports_to:roles.find(x=>x.code===r.managerCode)?.slug??'board',mandate:r.description,source_role:r.code})),null,2));
fs.writeFileSync(root+'/config/agent-ids.json',JSON.stringify(Object.fromEntries(roles.map(r=>[r.slug,r.id])),null,2));
fs.writeFileSync(root+'/config/agent-ids.txt',roles.map(r=>r.slug+' '+r.id).join('\n')+'\n');
audit.finishedAt=new Date().toISOString();audit.verifiedCount=fresh.length;audit.skillsPerAgent=skills.length;audit.preservedOriginalIds=45;
fs.writeFileSync(root+'/logs/agent-activity/roster-v2-verification.json',JSON.stringify(audit,null,2));
console.log('VERIFIED: 370 agents, 45 preserved identities, 325 additions, '+skills.length+' skills each.');
