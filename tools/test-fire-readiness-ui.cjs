// Real isolated API + production learner renderer. Never writes production data.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
 const fixture=JSON.parse(fs.readFileSync('/tmp/fire-readiness-fixture.json','utf8'));
 const browser=await chromium.launch({headless:true,channel:process.env.ACCOUNTS_UI_CHANNEL||undefined,executablePath:process.env.PB_UI_CHROME||undefined});
 try {
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.addInitScript(session=>sessionStorage.setItem('ifcm-fire-session-student',JSON.stringify(session)),fixture.session);
  await page.goto('http://127.0.0.1:8125/v4/fire/editable.html?edition='+fixture.edition);
  await page.locator('[data-nav=exam]').waitFor();
  await page.locator('[data-nav=exam]').click();
  if(width===1440){
   assert.match(await page.locator('#progressLabel').innerText(),/95% подготовки/);
   assert.equal(await page.locator('#submitExam').isDisabled(),true);
   assert.equal(await page.locator('[data-task-go]').count(),2);
   assert.notEqual(await page.locator('[data-nav=smoke] .number').innerText(),'✓');
   for(const id of ['smoke2','smoke3']){
    await page.locator('[data-task-id='+id+']').click();
    await page.locator('#task-'+id).waitFor();
    await page.locator('[data-answer='+id+'][value="0"]').check();
    await page.locator('[data-nav=exam]').click();
   }
   assert.match(await page.locator('#progressLabel').innerText(),/100% подготовки/);
   assert.equal(await page.locator('[data-task-go]').count(),0);
   assert.equal(await page.locator('[data-nav=smoke] .number').innerText(),'✓');
   assert.equal(await page.locator('#submitExam').isEnabled(),true);
   // Real submit flushes pending changes, passes the server gate and freezes the exam.
   await page.locator('#submitExam').click();
   await page.getByRole('heading',{name:'Ваши результаты',exact:true}).waitFor();
   await page.reload();
   await page.locator('[data-nav=exam]').waitFor();
   await page.locator('[data-nav=exam]').click();
   assert.equal(await page.locator('#submitExam').count(),0);
   assert.equal(await page.locator('[data-exam]:enabled').count(),0);
  }else{
   await page.locator('[data-nav=smoke]').click();
   assert.equal(await page.locator('[data-smoke]').count(),3);
   await page.locator('[data-smoke="2"]').click();
   await page.locator('#task-smoke3').waitFor();
  }
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);await context.close();
  console.log('Fire readiness passed at width',width);
 }
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
