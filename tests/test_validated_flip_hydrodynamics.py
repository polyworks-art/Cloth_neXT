"""Fail-closed source binding for experimental reconstruction validation."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from tools.prepare_validated_flip_hydrodynamics import validate_sources

class SourceBindingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.data={'usage':'ISOLATED_BLENDER_VALIDATION_ONLY','release_approved':False,'surface_directory':str(self.root),'files':{}}
  for frame in (131,132):
   name=f'{frame:06}.bobj';content=f'independent fixture{frame}'.encode();(self.root/name).write_bytes(content);self.data['files'][name]=hashlib.sha256(content).hexdigest()
  self.manifest=self.root/'manifest.json';self.save()
 def save(self):self.manifest.write_text(json.dumps(self.data))
 def test_valid_selected_frames(self):self.assertEqual(validate_sources(self.manifest,131,2),self.root)
 def test_tampered_surface_refused(self):
  (self.root/'000132.bobj').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'changed'):validate_sources(self.manifest,131,2)
 def test_missing_lookahead_refused(self):
  del self.data['files']['000132.bobj'];self.save()
  with self.assertRaisesRegex(ValueError,'Missing'):validate_sources(self.manifest,131,2)
 def test_no_production_approval(self):
  self.data['release_approved']=True;self.save()
  with self.assertRaisesRegex(ValueError,'isolated'):validate_sources(self.manifest,131,2)
 def test_outside_investigated_range_refused(self):
  with self.assertRaisesRegex(ValueError,'Outside'):validate_sources(self.manifest,160,2)
 def test_single_sample_refused(self):
  with self.assertRaisesRegex(ValueError,'Outside'):validate_sources(self.manifest,131,1)

if __name__=='__main__':unittest.main()
