import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const root='C:/Users/Jonat/algoalpha';
const read=p=>JSON.parse(fs.readFileSync(root+'/'+p,'utf8').replace(/^\uFEFF/,''));
const company=read('config/company.json'),team=read('config/team-plan.json'),ids=read('config/agent-ids.json'),risk=read('config/risk-thresholds.json');
const get=async p=>{const r=await fetch('http://127.0.0.1:3100/api'+p);assert.equal(r.status,200);return r.json();};
const liveCompany=await get('/companies/'+company.id);assert.equal(liveCompany.name,'AlgoAlpha');
const agents=await get('/companies/'+company.id+'/agents');assert.equal(agents.length,team.length);
const ceo=fs.readFileSync(root+'/agents/ceo/AGENTS.md','utf8');
for(const role of team){const matches=agents.filter(a=>a.name===role.name);assert.equal(matches.length,1,role.slug);const a=matches[0];assert.equal(a.id,ids[role.slug]);assert.equal(a.adapterType,'codex_local');assert.equal(a.adapterConfig.dangerouslyBypassApprovalsAndSandbox,false);assert.equal(a.adapterConfig.model,undefined);assert.ok(path.isAbsolute(a.adapterConfig.cwd));assert.equal(path.resolve(a.adapterConfig.cwd),path.resolve(root));assert.equal(a.reportsTo,role.reports_to==='board'?null:ids[role.reports_to]);assert.ok(fs.existsSync(a.adapterConfig.instructionsFilePath));assert.ok(ceo.includes(a.id));}
assert.equal(risk.paper_trading_only,true);assert.equal(risk.live_trading_enabled,false);assert.deepEqual(Object.values(risk.phases).map(x=>[x.sharpe_minimum,x.max_drawdown_pct]),[[1,25],[1.5,15],[2,10]]);
const issue=read('config/smoke-issue.json'),liveIssue=await get('/issues/'+issue.id),comments=await get('/issues/'+issue.id+'/comments'),runs=await get('/issues/'+issue.id+'/runs');
const file=root+'/logs/agent-activity/runtime-smoke-test.txt';
const checks={company:true,agentCount:agents.length,uniqueAgents:true,adapters:true,cwd:true,bypassDisabled:true,modelUnset:true,reportingLines:true,instructions:true,ceoSpecialistIds:true,paperOnly:true,phaseThresholds:true,smokeFile:fs.existsSync(file),smokeIssueDone:liveIssue.status==='done',agentComment:comments.some(c=>c.authorAgentId===ids['software-engineer']&&c.body.includes('runtime-smoke-test.txt')),runStatuses:runs.map(r=>r.status)};
if(checks.smokeFile)assert.ok(fs.readFileSync(file,'utf8').includes('AlgoAlpha runtime verified; paper trading only; live trading disabled.'));
const report={verifiedAt:new Date().toISOString(),checks};fs.writeFileSync(root+'/logs/agent-activity/installation-verification.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
if(!checks.smokeFile||!checks.smokeIssueDone||!checks.agentComment)process.exitCode=2;
