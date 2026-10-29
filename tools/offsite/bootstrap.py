"""One-time authorized offsite setup. Credentials are decrypted only on production."""
import os,subprocess,base64,pty,select,time,shlex,json,sys
from pathlib import Path
BASE=Path('/opt/upo-workbooks/maintenance');PRIVATE=BASE/'private'
def run(args,**kw):
 r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
 if r.returncode:raise RuntimeError('Private setup command failed')
 return r.stdout

def main():
 os.umask(0o077);PRIVATE.mkdir(exist_ok=True,mode=0o700)
 key=PRIVATE/'offsite_ed25519'
 if not key.exists():run(['ssh-keygen','-q','-t','ed25519','-N','','-C','ifcm-offsite-backup','-f',str(key)])
 known=run(['ssh-keyscan','-T','10','-t','ed25519','185.218.17.114'])
 fp=run(['ssh-keygen','-lf','-'],input=known).decode()
 if 'SHA256:GTw2xilbSigY5kMKDCpq0sRaz3VmP/qilCbh/R21xsw' not in fp:raise RuntimeError('Offsite host key changed')
 (PRIVATE/'offsite_known_hosts').write_bytes(known)
 receiver=base64.b64encode(Path(__file__).with_name('receiver.py').read_bytes()).decode()
 pub=key.with_suffix('.pub').read_text().strip()
 authorization='restrict,command="/usr/bin/python3 /backups/ifcm-workbooks/receiver.py" '+pub
 script='''set -eu
umask 077
if [ -L /backups ] || [ -L /backups/ifcm-workbooks ]; then exit 1; fi
install -d -m 700 /backups/ifcm-workbooks /backups/ifcm-workbooks/points
printf %s '''+shlex.quote(receiver)+''' | base64 -d > /backups/ifcm-workbooks/receiver.py
chmod 600 /backups/ifcm-workbooks/receiver.py
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if ! grep -qF '''+shlex.quote(pub)+''' /root/.ssh/authorized_keys; then printf '%s\\n' '''+shlex.quote(authorization)+''' >> /root/.ssh/authorized_keys; fi
printf 'IFCM_SETUP_OK\\n'
'''
 encoded=base64.b64encode(script.encode()).decode()
 remote='printf %s '+shlex.quote(encoded)+' | base64 -d | /bin/sh'
 password=sys.stdin.buffer.read(1024)
 if not password or b'\n' in password or b'\r' in password:raise RuntimeError('Missing private setup credential')
 master,slave=pty.openpty()
 process=subprocess.Popen(['ssh','-tt','-o','PubkeyAuthentication=no','-o','PreferredAuthentications=password','-o','NumberOfPasswordPrompts=1','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(PRIVATE/'offsite_known_hosts'),'root@185.218.17.114',remote],stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
 os.close(slave);buf=b'';sent=False;deadline=time.monotonic()+90
 try:
  while time.monotonic()<deadline:
   readable,_,_=select.select([master],[],[],1)
   if readable:
    try:chunk=os.read(master,65536)
    except OSError:break
    if not chunk:break
    buf+=chunk
    if b'password:' in buf.lower() and not sent:
     os.write(master,password+b'\n');password=b'';sent=True
   if process.poll() is not None:break
  if process.poll() is None:process.terminate()
  process.wait(timeout=10)
  if process.returncode or b'IFCM_SETUP_OK' not in buf:raise RuntimeError('Offsite authentication or setup failed')
 finally:
  os.close(master)
  if process.poll() is None:process.kill()
 print(json.dumps({'restricted_offsite_key_installed':True,'separate_backup_directory':True}))
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'offsite_setup':False,'error_type':type(e).__name__}));raise SystemExit(1)
