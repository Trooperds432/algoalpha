import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
const root='C:/Users/Jonat/algoalpha';
const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
const company=JSON.parse(fs.readFileSync(root+'/config/company.json','utf8').replace(/^\uFEFF/,''));
const config={cwd:root,engine:'auto',search:true,dangerouslyBypassApprovalsAndSandbox:false};
fs.writeFileSync(root+'/config/adapter-config.json',JSON.stringify(config,null,2));
const p=spawnSync(process.execPath,[cli,'adapter','test-environment','codex_local','--company-id',company.id,'--payload-json',JSON.stringify({adapterConfig:config}),'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024});
fs.writeFileSync(root+'/logs/agent-activity/adapter-test.json',p.stdout);console.log(p.stdout);console.error(p.stderr);process.exitCode=p.status;
