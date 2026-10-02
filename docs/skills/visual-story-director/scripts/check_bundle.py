"""Read-only portable skill bundle checks; Python standard library only."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

REQUIRED = ['SKILL.md','README.md','CHANGELOG.md','LICENSE','THIRD_PARTY_NOTICES.md',
    'requirements-checks.txt','provenance/upstream-lock.json','provenance/adaptation-map.md',
    'schemas/preproduction-package.schema.json','schemas/project-context.schema.json','schemas/review.schema.json',
    'scripts/check_bundle.py','scripts/validate_contract.py','tests/test_bundle.py','tests/test_contract.py',
    'tests/fixtures/valid/complete-package.json','tests/fixtures/invalid/expectations.json',
    'evals/evals.json','evals/rubric.md','docs/source-map.md','docs/implementation-decisions.md','docs/acceptance.md']
REFERENCES = ['scope-and-routing','intake-and-modes','source-handling','story-development',
    'visual-storytelling','style-guide','characters-and-references','locations-and-props',
    'storyboard-layout','format-adaptation','prompt-writing','voice-script','project-context',
    'revision-and-review','handoff-contract','quality-rubric',
    'development-process','viewer-comprehension']
TEMPLATES = ['brief','character','style','location','prop','page','prompt','voice-script','project-context','review']
EXAMPLES = ['original-short-story','educational-explainer','no-references','continuation','targeted-revision']


def outside_code(text):
    result=[]; fenced=False
    for line in text.splitlines():
        if re.match(r'^\s*(```|~~~)',line):
            fenced=not fenced
            continue
        if not fenced: result.append(line)
    return '\n'.join(result), fenced


def check(root):
    root=Path(root).resolve(); errors=[]; warnings=[]
    def err(path,message): errors.append({'path':str(path),'message':message})
    for name in REQUIRED+[f'references/{n}.md' for n in REFERENCES]+[f'assets/templates/{n}.md' for n in TEMPLATES]:
        path=root/name
        if not path.is_file() or path.stat().st_size==0: err(name,'required nonempty file missing')
    for name in EXAMPLES:
        for file in ['input.md','output.md']:
            if not (root/'examples'/name/file).is_file(): err(name,'unfinished example: '+file)
        if not list((root/'examples'/name).glob('*.json')): err(name,'example lacks structured output')
    skill=root/'SKILL.md'
    if skill.exists():
        text=skill.read_text(encoding='utf-8-sig')
        match=re.match(r'^---\n(.*?)\n---\n',text,re.S)
        if not match: err('SKILL.md','missing frontmatter')
        else:
            front=match[1]
            name=re.search(r'^name:\s*(\S+)\s*$',front,re.M)
            description=re.search(r'^description:\s*(?:[>|]-?\n((?:[ \t]+[^\n]*\n?)+)|([^\n]+))',front,re.M)
            if not name or name[1]!=root.name or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name[1]) or len(name[1])>64:
                err('SKILL.md','invalid name or directory mismatch')
            desc=' '.join((description[1] or description[2]).split()) if description else ''
            if not 1<=len(desc)<=1024: err('SKILL.md','description length invalid')
            # Constrained authored frontmatter, not a general YAML parser.
            if re.search(r'^[^ \t][^:\n]+$',front,re.M): err('SKILL.md','unsupported or invalid frontmatter line')
    for path in root.rglob('*.md'):
        rel=path.relative_to(root)
        if '__pycache__' in rel.parts: continue
        text=path.read_text(encoding='utf-8-sig')
        body,fenced=outside_code(text)
        if fenced: err(rel,'unclosed fenced block')
        if len(text.splitlines())>1000:
            warnings.append({'path':str(rel),'message':'Long Markdown document; consider a contents map or splitting by reader task. Length is advisory, not a release blocker.'})
        if rel.parts[0]!='licenses' and re.search(r'[\u4e00-\u9fff\ufffd]',text): err(rel,'unexpected CJK or replacement character in authored Markdown')
        for line in body.splitlines():
            if re.search(r'\b(?:TODO|TBD|FIXME)\b',line): err(rel,'unfinished placeholder')
        for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',body):
            raw=target.strip().strip('<>').split('#',1)[0]
            if not raw or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',raw): continue
            candidate=(path.parent/unquote(raw)).resolve()
            if not candidate.is_relative_to(root): err(rel,'internal link escapes bundle: '+raw)
            elif not candidate.exists(): err(rel,'broken internal path: '+raw)
        rows=body.splitlines()
        for i,line in enumerate(rows):
            if line.startswith('|') and line.endswith('|'):
                cols=len(re.split(r'(?<!\\)\|',line))-2
                previous=rows[i-1] if i else ''
                if not previous.startswith('|'):
                    if i+1>=len(rows) or not re.fullmatch(r'\|(?:\s*:?-+:?\s*\|)+',rows[i+1]): err(rel,'table missing separator')
                elif len(re.split(r'(?<!\\)\|',previous))-2!=cols: err(rel,'inconsistent table columns')
    for path in (root/'scripts').glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
                if any(n.split('.')[0] in {'socket','requests','httpx','urllib3','openai','subprocess','sqlite3'} for n in names):
                    err(path.name,'production/network/storage import in validation runtime')
                if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('urllib') and node.module!='urllib.parse':
                    err(path.name,'network import in validation runtime')
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in {'write_text','write_bytes','unlink','mkdir','rmdir'}:
                err(path.name,'mutating helper in read-only runtime')
    for name,count in [('assets/style-presets/presets.json',6),('assets/layout-presets/presets.json',4)]:
        path=root/name
        if not path.is_file(): err(name,'presets missing'); continue
        items=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(items,list) or len(items)<count or any(not item for item in items): err(name,'presets incomplete')
    for path in (root/'schemas').glob('*.json'):
        json.loads(path.read_text(encoding='utf-8'))
    lockpath=root/'provenance/upstream-lock.json'
    if lockpath.exists():
        lock=json.loads(lockpath.read_text(encoding='utf-8'))
        for donor in lock['donors']:
            if not re.fullmatch(r'[0-9a-f]{40}',donor.get('commit_sha','')): err(donor['name'],'missing commit pin')
            if not (root/'licenses'/donor['name']/'LICENSE').is_file(): err(donor['name'],'license missing')
            for file in donor['files']:
                if not re.fullmatch(r'[0-9a-f]{64}',file.get('sha256','')): err(file['path'],'missing file hash')
        if len(lock['donors'])!=5: err('upstream-lock.json','expected five selected donors')
    if root.name!='visual-story-director': warnings.append('Skill directory name must match its declared name.')
    return {'status':'failed' if errors else 'passed','errors':errors,'warnings':warnings,
            'limits':['Constrained frontmatter checks; general YAML syntax also checked by skill-creator at release.',
                      'Static runtime scan is supplemented by side-effect tests; this is not a general security scanner.']}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try:
        result=check(args.root);code=0 if result['status']=='passed' else 1
    except Exception as error:
        result={'status':'failed','execution_error':f'{type(error).__name__}: {error}'};code=2
        print(result['execution_error'],file=sys.stderr)
    print(json.dumps(result,ensure_ascii=False,indent=None if args.json else 2))
    return code


if __name__=='__main__': sys.exit(main())
