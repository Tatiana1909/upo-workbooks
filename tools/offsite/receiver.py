"""Restricted SSH endpoint for IFCM recovery points; no arbitrary shell commands."""
import os,sys,re,json,hashlib,shutil
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path('/backups/ifcm-workbooks/points')
NAMES={'data.tgz','server-settings.tgz','metadata.json','probe.py','checksums.json','verified.json'}
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def main():
 os.umask(0o077);ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 args=os.environ.get('SSH_ORIGINAL_COMMAND','').split()
 if len(args)<2:raise ValueError()
 op,stamp=args[:2]
 if not re.fullmatch(r'\d{8}T\d{12}Z',stamp):raise ValueError()
 p=ROOT/stamp; partial=ROOT/(stamp+'.partial')
 if p.is_symlink() or partial.is_symlink():raise ValueError()
 if op=='put' and len(args)==4:
  name,expected=args[2:]
  if name not in NAMES or not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError()
  if p.exists():
   if digest(p/name)!=expected:raise ValueError()
   # Consume input, allowing idempotent retries.
   while sys.stdin.buffer.read(1024*1024):pass
   print('OK');return
  partial.mkdir(mode=0o700,exist_ok=True)
  target=partial/name
  if target.is_symlink():raise ValueError()
  with target.open('wb') as f:shutil.copyfileobj(sys.stdin.buffer,f)
  if digest(target)!=expected:target.unlink();raise ValueError()
  print('OK')
 elif op=='commit' and len(args)==2:
  if not p.exists():
   if {x.name for x in partial.iterdir()}!=NAMES:raise ValueError()
   sums=json.loads((partial/'checksums.json').read_text())
   if set(sums)!=NAMES-{'checksums.json','verified.json'}:raise ValueError()
   if any(digest(partial/n)!=h for n,h in sums.items()):raise ValueError()
   v=json.loads((partial/'verified.json').read_text())
   if v.get('database_and_files_verified') is not True or v.get('isolated_api_restore') is not True:raise ValueError()
   partial.rename(p)
  valid=sorted(x for x in ROOT.iterdir() if re.fullmatch(r'\d{8}T\d{12}Z',x.name) and x.is_dir() and not x.is_symlink() and (x/'verified.json').is_file())
  for x in valid[:-1]:
   age=datetime.now(timezone.utc)-datetime.strptime(x.name,'%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
   if age>=timedelta(days=14):shutil.rmtree(x)
  print('OK')
 elif op=='get' and len(args)==3 and args[2] in NAMES:
  path=p/args[2]
  if path.is_symlink():raise ValueError()
  with path.open('rb') as f:shutil.copyfileobj(f,sys.stdout.buffer)
 else:raise ValueError()
if __name__=='__main__':
 try:main()
 except Exception:print('Rejected backup operation',file=sys.stderr);sys.exit(1)
