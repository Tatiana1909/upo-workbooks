(async()=>{
 const root=document.getElementById('course-directions');if(!root)return;
 try{
  const r=await fetch('/api/v1/fire/builder/catalog');if(!r.ok)throw Error();const items=await r.json();if(!items.length)return;
  let group=[...root.querySelectorAll('[data-direction]')].find(e=>e.dataset.direction==='Безопасность');
  if(!group){group=document.createElement('section');group.className='course-direction';group.dataset.direction='Безопасность';const h=document.createElement('h2');h.className='direction-title';h.textContent='Безопасность';const grid=document.createElement('div');grid.className='catalog-grid';group.append(h,grid);root.append(group)}
  for(const item of items){
   if(!/^pb-[a-f0-9]{24}$/.test(item.id))continue;
   const article=document.createElement('article');article.className='course-tile';article.dataset.fireEdition=item.id;
   const version=document.createElement('span');version.textContent='АКТУАЛЬНАЯ ВЕРСИЯ '+item.version;
   const title=document.createElement('h2');title.textContent=item.title;
   const p=document.createElement('p');p.textContent='Материалы, тренировка, мини-игры и итоговый тест по пожарной безопасности.';
   const a=document.createElement('a');a.href='/v4/fire/editable.html?edition='+encodeURIComponent(item.id);a.textContent='Открыть тетрадь →';
   const old=document.createElement('p');const link=document.createElement('a');link.href='/v4/fire/index.html';link.textContent='Прежняя тетрадь и прежние результаты';old.append(link);
   article.append(version,title,p,a,old);group.querySelector('.catalog-grid').append(article);
  }
 }catch{const status=document.getElementById('catalog-status');if(status)status.textContent+=' Не удалось загрузить пожарные тетради. Обновите страницу.'}
})();
