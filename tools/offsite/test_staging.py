import unittest,tempfile,importlib.util,json,hashlib,subprocess,os
from pathlib import Path
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('staging_offsite',Path(__file__).with_name('offsite.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TransferTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);m.BASE=self.root/'maintenance';(m.BASE/'private').mkdir(parents=True);m.ROOT=self.root/'points';m.ROOT.mkdir();self.p=m.ROOT/'20261029T120000000001Z';self.p.mkdir()
  values={'data.tgz':b'database archive','server-settings.tgz':b'settings archive','metadata.json':b'{}','probe.py':b'# test'}
  values['checksums.json']=json.dumps({k:hashlib.sha256(v).hexdigest() for k,v in values.items()}).encode();values['verified.json']=json.dumps({'database_and_files_verified':True,'isolated_api_restore':True}).encode()
  for k,v in values.items():(self.p/k).write_bytes(v)
  self.remote={};self.fail=False
  def ssh(cmd,**kw):
   args=cmd.split()
   if args[0]=='put':self.remote[args[2]]=kw['stdin'].read()
   elif self.fail:raise RuntimeError('simulated network failure')
   return SimpleNamespace(stdout=b'OK')
  m.ssh=ssh;m.remote_sha=lambda stamp,name:hashlib.sha256(self.remote[name]).hexdigest()
 def test_success_requires_full_remote_readback_before_cleanup(self):
  result=m.transfer(self.p,cleanup=True);self.assertTrue(result['local_staging_removed']);self.assertFalse(self.p.exists());self.assertEqual(set(self.remote),set(m.NAMES))
 def test_default_does_not_remove_local_files(self):
  m.transfer(self.p);self.assertEqual({p.name for p in self.p.iterdir()},set(m.NAMES))
 def test_network_failure_keeps_all_local_files(self):
  self.fail=True
  with self.assertRaises(RuntimeError):m.transfer(self.p,cleanup=True)
  self.assertEqual({p.name for p in self.p.iterdir()},set(m.NAMES))
 def test_bad_readback_keeps_all_local_files(self):
  m.remote_sha=lambda stamp,name:'0'*64
  with self.assertRaises(RuntimeError):m.transfer(self.p,cleanup=True)
  self.assertEqual({p.name for p in self.p.iterdir()},set(m.NAMES))
 def test_unknown_file_prevents_transfer_and_cleanup(self):
  (self.p/'do-not-delete.txt').write_text('unknown')
  with self.assertRaises(ValueError):m.transfer(self.p,cleanup=True)
  self.assertTrue((self.p/'do-not-delete.txt').exists());self.assertEqual(self.remote,{})
 def test_changed_local_point_is_not_removed(self):
  hashes=m.manifest(self.p);(self.p/'probe.py').write_bytes(b'changed')
  with self.assertRaises(ValueError):m.cleanup_confirmed(self.p,hashes)
  self.assertEqual({p.name for p in self.p.iterdir()},set(m.NAMES))
 def test_symlink_is_not_followed(self):
  target=self.root/'outside';target.write_text('untouched');(self.p/'probe.py').unlink();(self.p/'probe.py').symlink_to(target)
  with self.assertRaises(ValueError):m.transfer(self.p,cleanup=True)
  self.assertEqual(target.read_text(),'untouched')
class ReceiverTests(unittest.TestCase):
 def test_external_retention_boundary_preserves_unrelated_entries(self):
  from datetime import datetime,timezone,timedelta
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'points';root.mkdir();now=datetime(2026,10,29,12,tzinfo=timezone.utc)
   def point(at):
    p=root/at.strftime('%Y%m%dT%H%M%S%fZ');p.mkdir();(p/'verified.json').write_text('{}');return p
   expired=point(now-timedelta(days=14));inside=point(now-timedelta(days=14)+timedelta(seconds=1));latest=point(now)
   unknown=root/'unrelated';unknown.mkdir();partial=root/'20261001T000000000000Z.partial';partial.mkdir()
   outside=Path(tmp)/'outside';outside.mkdir();link=root/'20261001T000000000000Z';link.symlink_to(outside,target_is_directory=True)
   script=Path(tmp)/'receiver.py';source=Path(__file__).with_name('receiver.py').read_text().replace("Path('/backups/ifcm-workbooks/points')",'Path('+repr(str(root))+')').replace('datetime.now(timezone.utc)','datetime(2026,10,29,12,tzinfo=timezone.utc)');script.write_text(source)
   r=subprocess.run(['python',str(script)],env={**os.environ,'SSH_ORIGINAL_COMMAND':'commit '+latest.name},capture_output=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(expired.exists());self.assertTrue(inside.exists());self.assertTrue(latest.exists());self.assertTrue(unknown.exists());self.assertTrue(partial.exists());self.assertTrue(link.is_symlink());self.assertTrue(outside.exists())
 def test_external_last_good_point_is_kept_even_when_old(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'points';root.mkdir();old=root/'20260901T000000000000Z';old.mkdir();(old/'verified.json').write_text('{}')
   script=Path(tmp)/'receiver.py';script.write_text(Path(__file__).with_name('receiver.py').read_text().replace("Path('/backups/ifcm-workbooks/points')",'Path('+repr(str(root))+')'))
   r=subprocess.run(['python',str(script)],env={**os.environ,'SSH_ORIGINAL_COMMAND':'commit '+old.name},capture_output=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(old.exists())
 def test_scoped_status_is_read_only_and_rejects_traversal(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'points';root.mkdir();p=root/'20261029T120000000001Z';p.mkdir();(p/'verified.json').write_text('{}')
   script=Path(tmp)/'receiver.py';script.write_text(Path(__file__).with_name('receiver.py').read_text().replace("Path('/backups/ifcm-workbooks/points')",'Path('+repr(str(root))+')'))
   cmd=['python',str(script)]
   r=subprocess.run(cmd,env={**os.environ,'SSH_ORIGINAL_COMMAND':'status '+p.name},capture_output=True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['verified_points'],1);self.assertEqual(json.loads(r.stdout)['retention_days'],14)
   r=subprocess.run(cmd,env={**os.environ,'SSH_ORIGINAL_COMMAND':'status ../../etc'},capture_output=True);self.assertNotEqual(r.returncode,0);self.assertTrue(p.exists())
if __name__=='__main__':unittest.main()
