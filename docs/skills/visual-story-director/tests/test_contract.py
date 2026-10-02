import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from validate_contract import load_json, validate


class ContractTests(unittest.TestCase):
    def test_all_valid_fixtures_and_examples(self):
        paths=list((ROOT/'tests/fixtures/valid').glob('*.json'))+list((ROOT/'examples').rglob('*.json'))
        for path in paths:
            with self.subTest(file=str(path.relative_to(ROOT))):
                result=validate(load_json(path))
                self.assertEqual(result['status'],'passed',result)

    def test_all_negative_fixtures_have_specific_diagnostics(self):
        folder=ROOT/'tests/fixtures/invalid'
        expected=load_json(folder/'expectations.json')
        for name,message in expected.items():
            with self.subTest(case=name):
                result=validate(load_json(folder/(name+'.json')))
                self.assertEqual(result['status'],'failed')
                self.assertIn(message,' '.join(e['message'] for e in result['errors']))

    def test_intentional_visual_many_to_many_is_valid(self):
        data=load_json(ROOT/'tests/fixtures/valid/complete-package.json')
        panel=data['pages'][0]['panels'][1]
        panel['beat_ids'].append(data['beats'][0]['id'])
        self.assertEqual(validate(data)['status'],'passed')

    def test_intentional_panel_overlap_is_valid(self):
        data=load_json(ROOT/'tests/fixtures/valid/complete-package.json')
        page=data['pages'][0];a,b=page['panels'][:2]
        b['rect']=a['rect'][:]
        page['allowed_overlaps']=[dict(panel_ids=[a['id'],b['id']],reason='Intentional inset.')]
        self.assertEqual(validate(data)['status'],'passed')

    def test_explicit_project_import_is_valid(self):
        data=load_json(ROOT/'tests/fixtures/valid/complete-package.json')
        data['characters'][0]['origin_project_id']='other-a'
        data['cross_project_permissions']=[dict(source_project_id='other-a',approval='approved',reason='User explicitly requested crossover.')]
        self.assertEqual(validate(data)['status'],'passed')

    def test_external_lettering_keeps_text_contract(self):
        data=load_json(ROOT/'tests/fixtures/valid/complete-package.json')
        for prompt in data['image_prompts']:
            prompt['lettering_mode']='external_lettering'
            for visible in prompt['visible_lines']: prompt['text']=prompt['text'].replace(visible['text'],'Reserved blank text zone.')
        self.assertEqual(validate(data)['status'],'passed')

    def test_revision_old_value_with_baseline(self):
        before=load_json(ROOT/'examples/targeted-revision/before.json')
        after=load_json(ROOT/'examples/targeted-revision/after-line.json')
        self.assertEqual(validate(after,baseline=before)['status'],'passed')
        after['targeted_revision']['changes'][0]['old_value']='Incorrect old text.'
        result=validate(after,baseline=before)
        self.assertEqual(result['status'],'failed')
        self.assertIn('old_value mismatch',result['errors'][0]['message'])

    def test_validator_has_no_network_or_input_mutation(self):
        data=load_json(ROOT/'tests/fixtures/valid/complete-package.json');before=copy.deepcopy(data)
        with patch('socket.socket',side_effect=AssertionError('network call')), patch('urllib.request.urlopen',side_effect=AssertionError('network call')):
            result=validate(data)
        self.assertEqual(result['status'],'passed');self.assertEqual(data,before)

    def test_cli_json_and_exit_codes(self):
        script=str(ROOT/'scripts/validate_contract.py')
        for name,expected in [('valid/complete-package.json',0),('invalid/missing-line.json',1),('absent.json',2)]:
            with self.subTest(name=name):
                process=subprocess.run([sys.executable,'-B',script,'--input',str(ROOT/'tests/fixtures'/name),'--json'],capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(process.returncode,expected,process.stderr)
                self.assertIsInstance(json.loads(process.stdout),dict)

    def test_duplicate_keys_and_nonfinite_numbers_rejected(self):
        for text in ['{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}','{"a":1e999}']:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'bad.json';path.write_text(text,encoding='utf-8')
                with self.assertRaises(ValueError): load_json(path)

    def test_revision_target_and_new_value_are_checked_without_baseline(self):
        after=load_json(ROOT/'examples/targeted-revision/after-line.json')
        after['targeted_revision']['changes'][0]['new_value']='Unapplied new text.'
        result=validate(after)
        self.assertEqual(result['status'],'failed')
        self.assertIn('new_value mismatch',' '.join(e['message'] for e in result['errors']))

    def test_entrypoints_leave_bundle_bytes_unchanged(self):
        def snapshot(): return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob('*') if p.is_file()}
        before=snapshot()
        process=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/validate_contract.py'),'--input',str(ROOT/'tests/fixtures/valid/complete-package.json'),'--json'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(process.returncode,0,process.stderr);self.assertEqual(snapshot(),before)


if __name__=='__main__': unittest.main()
