"""Copy verified points to the restricted offsite endpoint and verify a read-back restore."""
import subprocess,hashlib,json,sys,tempfile,fcntl,os,re
from pathlib import Path
BASE=Path('/opt/upo-workbooks/maintenance')
ROOT=Path('/opt/upo-workbooks/backups/scheduled')
NAMES=('data.tgz','server-settings.tgz','metadata.json','probe.py','checksums.json','verified.json')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def ssh(command,**kw):
 args=['ssh','-i',str(BASE/'private/offsite_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(BASE/'private/offsite_known_hosts'),'-o','ConnectTimeout=15','root@185.218.17.114',command]
 r=subprocess.run(args,stderr=subprocess.PIPE,**kw)
 if r.returncode:
  detail=r.stderr.decode(errors='replace')
  flags=[x for x in ('Rejected backup operation','Permission denied','No space left','No such file','SyntaxError','Connection closed') if x.lower() in detail.lower()]
  print(json.dumps({'offsite_command':command.split()[0],'returncode':r.returncode,'diagnostic':flags}),flush=True)
  raise RuntimeError('Offsite operation failed')
 return r

def main():
 os.umask(0o077)
 with (BASE/'private/offsite.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  points=sorted(p for p in ROOT.iterdir() if re.fullmatch(r'\d{8}T\d{12}Z',p.name) and p.is_dir() and not p.is_symlink() and (p/'verified.json').is_file())
  if not points:raise RuntimeError('No verified local backup')
  p=points[-1]; verified=json.loads((p/'verified.json').read_text())
  if verified.get('isolated_api_restore') is not True:raise RuntimeError()
  for name in NAMES:
   print(json.dumps({'offsite_stage':'upload','file':name}),flush=True)
   with (p/name).open('rb') as f:ssh('put '+p.name+' '+name+' '+sha(p/name),stdin=f,stdout=subprocess.PIPE)
  print(json.dumps({'offsite_stage':'commit'}),flush=True)
  ssh('commit '+p.name,stdout=subprocess.PIPE)
  if '--restore-check' in sys.argv:
   from restore_check import check
   with tempfile.TemporaryDirectory(prefix='offsite-readback-',dir=ROOT) as d:
    target=Path(d)
    for name in NAMES:
     with (target/name).open('wb') as f:ssh('get '+p.name+' '+name,stdout=f)
     if sha(target/name)!=sha(p/name):raise RuntimeError('Offsite read-back mismatch')
    print(json.dumps({'offsite_stage':'isolated_readback_restore'}),flush=True)
    result=check(target)
    if result.get('isolated_api_restore') is not True:raise RuntimeError()
  result={'offsite_copy_verified':True,'offsite_readback_restore':'--restore-check' in sys.argv,'point':p.name}
  (BASE/'private/offsite-last-success.json').write_text(json.dumps(result))
  print(json.dumps(result))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'offsite_copy_verified':False,'error_type':type(e).__name__}));sys.exit(1)
