"""Transfer staged recovery points; remove local staging only after verified remote read-back."""
import subprocess,hashlib,json,sys,tempfile,fcntl,os,re,shutil
from pathlib import Path
from contextlib import ExitStack
BASE=Path('/opt/upo-workbooks/maintenance')
ROOT=Path('/opt/upo-workbooks/backups/scheduled')
NAMES=('data.tgz','server-settings.tgz','metadata.json','probe.py','checksums.json','verified.json')
STAMP=re.compile(r'\d{8}T\d{12}Z')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def args(command):
 return ['ssh','-i',str(BASE/'private/offsite_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(BASE/'private/offsite_known_hosts'),'-o','ConnectTimeout=15','root@185.218.17.114',command]
def ssh(command,**kw):
 kw.setdefault('stdin',subprocess.DEVNULL)
 r=subprocess.run(args(command),stderr=subprocess.PIPE,**kw)
 if r.returncode:raise RuntimeError('Offsite operation failed')
 return r
def remote_sha(stamp,name):
 # Hash the downloaded stream without storing a second archive on the working disk.
 p=subprocess.Popen(args('get '+stamp+' '+name),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 h=hashlib.sha256()
 try:
  for b in iter(lambda:p.stdout.read(1024*1024),b''):h.update(b)
  if p.wait():raise RuntimeError('Offsite read-back failed')
 finally:
  p.stdout.close()
  if p.poll() is None:p.kill();p.wait()
 return h.hexdigest()
def manifest(p):
 if p.is_symlink() or not p.is_dir() or not STAMP.fullmatch(p.name):raise ValueError('Invalid staging point')
 if {x.name for x in p.iterdir()}!=set(NAMES):raise ValueError('Unexpected staging files')
 if any(not (p/n).is_file() or (p/n).is_symlink() for n in NAMES):raise ValueError('Invalid staging files')
 v=json.loads((p/'verified.json').read_text())
 if v.get('database_and_files_verified') is not True or v.get('isolated_api_restore') is not True:raise ValueError('Unverified staging point')
 sums=json.loads((p/'checksums.json').read_text())
 if set(sums)!=set(NAMES)-{'checksums.json','verified.json'}:raise ValueError('Invalid staging manifest')
 hashes={n:sha(p/n) for n in NAMES}
 if any(hashes[n]!=h for n,h in sums.items()):raise ValueError('Staging checksum mismatch')
 return hashes
def cleanup_confirmed(p,hashes):
 # Revalidate the complete local point after all remote hashes matched.
 if manifest(p)!=hashes:raise RuntimeError('Staging changed during transfer')
 for n in NAMES:(p/n).unlink()
 p.rmdir()
def transfer(p,cleanup=False,restore_check=False):
 hashes=manifest(p);size=sum((p/n).stat().st_size for n in NAMES)
 for n in NAMES:
  with (p/n).open('rb') as f:ssh('put '+p.name+' '+n+' '+hashes[n],stdin=f,stdout=subprocess.PIPE)
 ssh('commit '+p.name,stdout=subprocess.PIPE)
 for n in NAMES:
  if remote_sha(p.name,n)!=hashes[n]:raise RuntimeError('Offsite read-back checksum mismatch')
 if restore_check:
  from restore_check import check
  with tempfile.TemporaryDirectory(prefix='offsite-readback-',dir=ROOT) as d:
   target=Path(d)
   for n in NAMES:
    with (target/n).open('wb') as f:ssh('get '+p.name+' '+n,stdout=f)
    if sha(target/n)!=hashes[n]:raise RuntimeError('Offsite restored download mismatch')
   if check(target).get('isolated_api_restore') is not True:raise RuntimeError('Offsite isolated restore failed')
 result={'offsite_copy_verified':True,'offsite_stream_hashes_verified':True,'offsite_readback_restore':restore_check,'point':p.name,'bytes':size,'local_staging_removed':False}
 (BASE/'private/offsite-last-success.json').write_text(json.dumps(result))
 if cleanup:cleanup_confirmed(p,hashes);result['local_staging_removed']=True
 (BASE/'private/offsite-last-success.json').write_text(json.dumps(result))
 print(json.dumps(result),flush=True)
 return result
def main():
 os.umask(0o077)
 with ExitStack() as stack:
  for path in (BASE/'private/offsite.lock',BASE/'backup.lock'):
   f=stack.enter_context(path.open('a'));fcntl.flock(f,fcntl.LOCK_EX)
  ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
  points=sorted(p for p in ROOT.iterdir() if STAMP.fullmatch(p.name) and p.is_dir() and not p.is_symlink() and (p/'verified.json').is_file())
  if not points:
   if '--allow-empty' in sys.argv:print(json.dumps({'pending_staging_points':0}));return
   raise RuntimeError('No verified staging point')
  chosen=points if '--all' in sys.argv else points[-1:]
  for p in chosen:transfer(p,cleanup='--cleanup-confirmed' in sys.argv,restore_check='--restore-check' in sys.argv and p==chosen[-1])
  print('OFFSITE_STORAGE '+ssh('status '+chosen[-1].name,stdout=subprocess.PIPE).stdout.decode().strip(),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps({'offsite_copy_verified':False,'error_type':type(e).__name__,'local_staging_preserved_on_failure':True}));sys.exit(1)
