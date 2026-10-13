import {spawnSync} from 'node:child_process';
const p=spawnSync('codex',['mcp','get','tradingview','--json'],{encoding:'utf8',shell:true,env:{...process.env,CODEX_HOME:'C:/Users/Jonat/algoalpha/.paperclip/instances/default/companies/3b1d401d-7010-4eed-acd1-be7d52372c07/codex-home'}});
if(p.status!==0)throw new Error(p.stderr);const c=JSON.parse(p.stdout);console.log(JSON.stringify({enabled:c.enabled,command:c.transport.command,enabledTools:c.enabled_tools},null,2));
