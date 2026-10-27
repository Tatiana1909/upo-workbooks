(async()=>{
 const root=document.getElementById('course-directions');if(!root)return;
 const groups=new Map([...root.querySelectorAll('[data-direction]')].map(el=>[el.dataset.direction,el]));
 function grid(direction){direction=(direction||'1С:УПО').trim();if(!groups.has(direction)){const section=document.createElement('section'),h=document.createElement('h2'),g=document.createElement('div');section.className='course-direction';section.dataset.direction=direction;h.className='direction-title';h.textContent=direction;g.className='catalog-grid';section.append(h,g);root.append(section);groups.set(direction,section)}return groups.get(direction).querySelector('.catalog-grid')}
 function tile(item,url){const article=document.createElement('article'),label=document.createElement('span'),h=document.createElement('h2'),a=document.createElement('a');article.className='course-tile';article.dataset.courseId=item.course_id||item.id;if(String(item.id||'').startsWith('pb-'))article.dataset.fireEdition=item.id;label.textContent=(item.is_latest===false?'ПРЕЖНЯЯ ВЕРСИЯ ':'АКТУАЛЬНАЯ ВЕРСИЯ ')+item.version;h.textContent=item.title;a.textContent='Открыть тетрадь →';a.href=url;article.append(label,h);if(item.description){const p=document.createElement('p');p.textContent=item.description;article.append(p)}article.append(a);return article}
 try{const response=await fetch('/api/v1/workbooks');if(!response.ok)throw Error();for(const item of await response.json()){
   if(item.is_latest===false)continue;
   grid(item.course_direction).append(tile(item,'builder/workbook.html?course='+encodeURIComponent(item.course_id)));
 }}catch{document.getElementById('catalog-status').textContent='Не удалось загрузить новые тетради. Обновите страницу, чтобы увидеть полный каталог.'}
 try{const response=await fetch('/api/v1/fire/builder/catalog');if(!response.ok)return;const rows=await response.json();for(const item of rows)if(item.is_latest!==false)grid('Безопасность').append(tile(item,item.url));}catch{document.getElementById('catalog-status').textContent+=' Не удалось загрузить тетради пожарной безопасности.'}
})();
