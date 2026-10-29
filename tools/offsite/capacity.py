"""Reclaim only unused IFCM build images that no retained recovery point references."""
import json,subprocess,sys,shutil
from pathlib import Path
def run(args):
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL)
 if r.returncode:raise RuntimeError('Image inventory failed')
 return r.stdout.decode()
def main():
 protected=set()
 containers=run(['docker','ps','-aq']).split()
 if containers:
  for item in json.loads(run(['docker','inspect',*containers])):protected.add(item['Image'])
 root=Path('/opt/upo-workbooks/backups')
 for p in root.rglob('*.json'):
  if p.is_symlink() or p.name not in ('metadata.json','container-settings.json'):continue
  try:
   value=json.loads(p.read_text())
   for item in value if isinstance(value,list) else [value]:
    if isinstance(item,dict):
     for k in ('image_id','Image'):
      if isinstance(item.get(k),str):protected.add(item[k])
  except (ValueError,OSError):pass
 ids=list(set(run(['docker','image','ls','-q','--filter','label=com.docker.compose.project=upo_workbooks']).split()))
 candidates=[]
 if ids:
  for im in json.loads(run(['docker','image','inspect',*ids])):
   if im['Id'] not in protected:candidates.append(im['Id'])
 print(json.dumps({'unreferenced_ifcm_images':len(candidates),'retained_image_references':len(protected),'free_mib':shutil.disk_usage(root).free//1024**2}),flush=True)
 if '--apply' in sys.argv:
  removed=0
  for iid in candidates:
   # No force: Docker refuses removal if a container started using the image meanwhile.
   r=subprocess.run(['docker','image','rm',iid],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)
   removed+=r.returncode==0
  print(json.dumps({'unused_ifcm_images_removed':removed,'free_mib':shutil.disk_usage(root).free//1024**2}),flush=True)
if __name__=='__main__':main()
