/* End browser sessions without removing locally stored workbook results. */
window.IFCMExit = async function(auth) {
  const keys = ['ifcm-personal-session','builder-student-session','ifcm-fire-session-student',
    'ifcm-fire-session-teacher','ifcm-workbook-admin-session','ifcm-workbook-session'];
  const tokens = new Set();
  if (auth?.account && auth.token) tokens.add(auth.token);
  for (const key of keys) {
    try { const s=JSON.parse(sessionStorage.getItem(key));if(s?.account&&s.token)tokens.add(s.token); } catch {}
    sessionStorage.removeItem(key);
  }
  await Promise.allSettled([...tokens].map(token=>fetch('/api/v1/accounts/logout', {
    method:'POST',signal:AbortSignal.timeout(5000),headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:'{}'
  })));
  location.replace('/v4/index.html');
};
