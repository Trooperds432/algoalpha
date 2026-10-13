import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
export const root='C:/Users/Jonat/algoalpha';
export const cli='C:/Users/Jonat/AppData/Local/npm-cache/_npx/43414d9b790239bb/node_modules/paperclipai/dist/index.js';
export function call(args){const p=spawnSync(process.execPath,[cli,...args,'--api-base','http://127.0.0.1:3100','--json'],{cwd:root,encoding:'utf8',maxBuffer:16*1024*1024});if(p.status!==0)throw new Error(p.stderr||p.stdout);return JSON.parse(p.stdout);}
if(fs.existsSync(root+'/config/company.json')){console.log('Company configuration already exists; no duplicate created.');process.exit(0);} const company=call(['company','create','--payload-json',JSON.stringify({name:'AlgoAlpha',description:'Paper-only quantitative research firm. Advanced mathematical and ML research with independent staged risk gates. No live financial accounts or execution.'})]);
fs.writeFileSync(root+'/config/company.json',JSON.stringify(company,null,2));
console.log(JSON.stringify({id:company.id,name:company.name}));

