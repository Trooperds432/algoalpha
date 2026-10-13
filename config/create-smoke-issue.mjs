import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
const root='C:/Users/Jonat/algoalpha';
const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
const read=p=>JSON.parse(fs.readFileSync(root+'/'+p,'utf8').replace(/^\uFEFF/,''));
const company=read('config/company.json'),ids=read('config/agent-ids.json');
const description='Installation verification only. Check out this assigned issue using the Paperclip skill/API. Read config/risk-thresholds.json without modifying it. Create logs/agent-activity/runtime-smoke-test.txt containing your agent ID, current UTC timestamp, and the exact sentence: AlgoAlpha runtime verified; paper trading only; live trading disabled. Then post a comment to THIS issue confirming the file path and the three phase thresholds, and mark this issue done. Do not delegate or perform any market research. Do not access credentials or any financial account. Use the supplied Paperclip run authentication normally without printing secrets.';
const p=spawnSync(process.execPath,[cli,'issue','create','--company-id',company.id,'--title','Verify Codex authentication and paper-only workspace','--description',description,'--status','todo','--assignee-agent-id',ids['software-engineer'],'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024});
if(p.status!==0)throw new Error(p.stderr||p.stdout);const issue=JSON.parse(p.stdout);fs.writeFileSync(root+'/config/smoke-issue.json',JSON.stringify(issue,null,2));console.log(JSON.stringify({id:issue.id,identifier:issue.identifier,status:issue.status}));
