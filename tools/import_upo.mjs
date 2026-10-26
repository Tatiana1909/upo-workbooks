// Deterministic, content-only transfer. Never reads participant data or writes a database.
import fs from 'node:fs';import vm from 'node:vm';import crypto from 'node:crypto';
const context={window:{}};vm.createContext(context);for(const path of ['courses.js','v4/materials.js'])vm.runInContext(fs.readFileSync(path,'utf8'),context);
const courses=context.window.WORKBOOK_COURSES,materials=context.window.WORKBOOK_MATERIALS,learning=JSON.parse(fs.readFileSync('v4/learning-content.json'));
const asset=fs.readFileSync('v4/learning-assets/document.svg'),hash=crypto.createHash('sha256').update(asset).digest('hex');fs.mkdirSync('v4/builder/import-assets',{recursive:true});fs.writeFileSync(`v4/builder/import-assets/${hash}.svg`,asset);
const all={};
for(const cid of ['accounting','warehouse']){const c=courses[cid],sections=[];let seq=0;const text=(title,value,style='normal')=>({id:`import-${cid}-${++seq}`,type:'text',title,text:value,textStyle:style});
const intro=[text('О курсе',c.intro,'outcome'),text('Для кого',c.audience),text('Продолжительность',c.duration),text('Как работать','Изучите материалы по теме, выполните действия в тестовой базе, отметьте чек-лист и ответьте на контрольные вопросы.'),...c.rules.map(t=>text('Правила учебной работы',t,'rule'))];
for(let i=0;i<c.modules.length;i++){const m=c.modules[i],s=structuredClone(learning[cid].sections[i]),blocks=[text('Результат раздела',m.outcome,'outcome'),text('О теме','Изучите основные понятия раздела.','heading'),...m.concepts.map(x=>text(x.title,x.text,'concept'))];if(i===0)blocks.unshift(...intro);
 const theory=s.blocks.find(b=>b.id.startsWith('theory-'));theory.textStyle='theory';blocks.push(theory);
 const pack=materials[cid][i];blocks.push(text('Материалы по теме:','Откройте инструкции и СОПы для изучения темы.','heading'));
 for(const [kind,items,key,url] of [['instruction',pack.items,pack.folderKey,pack.folderUrl],['sop',pack.sops||[],pack.sopFolderKey||'sop',pack.sopFolderUrl||materials.sopFolderUrl]])for(const [title,file] of items)blocks.push({id:`material-${cid}-${++seq}`,type:'link',materialType:kind,title,text:title,url,download:`http://194.48.140.70:8081/api/v1/materials/${key}/download?filename=${encodeURIComponent(file)}`});
 blocks.push({id:`folder-${cid}-${i}`,type:'link',materialType:'other',title:'Открыть все материалы',text:'Все материалы раздела',url:pack.folderUrl});
 blocks.push(...s.blocks.filter(b=>!b.id.startsWith('theory-')&&!['single','practice'].includes(b.type)),text('Выполните по шагам','Практика · тестовая база','heading'));
 for(const b of s.blocks.filter(b=>b.type==='practice')){delete b.legacyPractice;delete b.legacyKey;b.points=0;b.allowScreenshots=cid==='accounting';blocks.push(b)}
 blocks.push({id:`note-${cid}-${i}`,type:'notes',title:'Ваши записи',text:m.prompt,rows:4},text('Контрольные вопросы','Проверьте себя','heading'));
 for(const b of s.blocks.filter(b=>b.type==='single')){delete b.legacyQuiz;blocks.push(b)}
 if(i===c.modules.length-1)blocks.push(...c.rules.map(t=>text('Делайте так',t,'rule')),...c.critical.map(t=>text('Не делайте так',t,'critical')));
 if(i===c.modules.length-1)blocks.push({id:`completion-${cid}`,type:'completion',title:'Итоговая карточка',text:'Прогресс, темы для повторения и ваши записи по курсу.'});
 sections.push({title:m.title.replace(/^(?:День|Блок)\s+\d+\s*/u,'').replace(/^\d+\.\s*/,''),minutes:m.minutes,blocks})}
 all[cid]={title:cid==='accounting'?'1С:УПО — Складской и производственный учёт':'1С:УПО — Работники склада',description:c.intro,courseDirection:'1С:УПО',reviewMode:'auto',sourceCourse:cid,schemaVersion:2,completionLabel:'Материал разобран, практическое задание завершено',sections};
 for(const s of sections)for(const b of s.blocks)if(b.image==='/v4/learning-assets/document.svg')b.image=`/v4/builder/import-assets/${hash}.svg`;
}
fs.writeFileSync('server/upo-templates.json',JSON.stringify(all,null,2)+'\n');
console.log(Object.fromEntries(Object.entries(all).map(([k,c])=>[k,{sections:c.sections.length,blocks:c.sections.reduce((n,s)=>n+s.blocks.length,0)}])));
