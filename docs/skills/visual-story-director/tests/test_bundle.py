from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_bundle import check


class BundleTests(unittest.TestCase):
    def test_complete_bundle(self):
        self.assertEqual(check(ROOT)['status'],'passed',check(ROOT))

    def test_broken_path_and_production_code_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/ROOT.name;shutil.copytree(ROOT,target)
            path=target/'references/story-development.md'
            path.write_text(path.read_text(encoding='utf-8')+'\n[Broken](absent.md)\n',encoding='utf-8')
            script=target/'scripts/unwanted.py';script.write_text('import socket\n',encoding='utf-8')
            result=check(target);messages=' '.join(e['message'] for e in result['errors'])
            self.assertEqual(result['status'],'failed');self.assertIn('broken internal path',messages);self.assertIn('production/network',messages)

    def test_missing_template_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/ROOT.name;shutil.copytree(ROOT,target)
            (target/'assets/templates/voice-script.md').unlink()
            self.assertEqual(check(target)['status'],'failed')


if __name__=='__main__': unittest.main()
