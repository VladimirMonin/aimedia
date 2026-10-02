"""Read-only JSON Schema 2020-12 and declared semantic validation, Python 3.10+."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = {'preproduction-package': 'preproduction-package', 'project-context': 'project-context', 'review': 'review'}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def load_json(path):
    def invalid_constant(value):
        raise ValueError(f'non-finite JSON number: {value}')
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f'non-finite JSON number: {value}')
        return number
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream, object_pairs_hook=unique_pairs, parse_constant=invalid_constant, parse_float=finite_float)


def semantic_errors(data):
    errors = []

    def err(path, message):
        errors.append({'path': path, 'message': message})

    def indexed(items, path):
        seen = {}
        for item in items:
            identifier = item['id']
            if identifier in seen:
                err(path, f'duplicate ID {identifier}')
            seen[identifier] = item
        return seen

    def ordered(items, path):
        values = [item['order'] for item in items]
        if sorted(values) != list(range(1, len(items) + 1)):
            err(path, f'incomplete or repeated order: {values}')

    def linked(ids, mapping, path):
        for identifier in ids:
            if identifier not in mapping:
                err(path, f'unresolved ID {identifier}')

    def rect(value, path):
        x, y, width, height = value
        if not all(math.isfinite(v) for v in value) or width <= 0 or height <= 0:
            err(path, 'non-finite or non-positive rectangle')
        if x + width > 1 + 1e-9 or y + height > 1 + 1e-9:
            err(path, 'rectangle outside canvas')

    def timing(value, path):
        if value['kind'] == 'estimated':
            lo, hi = value['range_seconds']
            if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
                err(path, 'invalid estimated range')
        elif value['kind'] == 'externally_supplied':
            if not all(math.isfinite(value[k]) for k in ('start', 'end')) or value['end'] <= value['start']:
                err(path, 'external interval must have end > start')

    def review(value, path):
        status = value['status']
        if status == 'passed' and (not value['checks_performed'] or any(
                f['severity'] in {'blocker', 'warning'} for f in value['findings'])):
            err(path, 'passed requires actual checks and no unresolved warnings/blockers')
        if status == 'needs_revision' and not value['findings']:
            err(path, 'needs_revision requires findings')
        if status == 'failed' and not value.get('failure_reason'):
            err(path, 'failed requires failure_reason; empty findings do not mean passed')
        if status == 'skipped' and (value['checks_performed'] or not value['limitations']):
            err(path, 'skipped requires no performed checks and an explicit limitation')

    project_id = data['project_id']
    permissions = {p['source_project_id'] for p in data.get('cross_project_permissions', [])}

    def ownership(value, path='$'):
        if isinstance(value, dict):
            if 'project_id' in value and value['project_id'] != project_id:
                err(path, f'cross-project ownership {value["project_id"]}; expected {project_id}')
            if 'origin_project_id' in value and value['origin_project_id'] != project_id and value['origin_project_id'] not in permissions:
                err(path, f'unauthorized import from {value["origin_project_id"]}')
            for key, child in value.items():
                ownership(child, path + '.' + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                ownership(child, f'{path}[{i}]')
    ownership(data)
    if data['kind'] == 'review':
        review(data['review'], '$.review')
        return errors
    if data['kind'] == 'project-context':
        context = data['context']
        chars = indexed(context['characters'], '$.context.characters')
        voices = indexed(context['voices'], '$.context.voices')
        for voice in voices.values():
            if voice['speaker_id'] != 'narrator':
                linked([voice['speaker_id']], chars, '$.context.voices')
        indexed(context['episode_summaries'], '$.context.episode_summaries')
        indexed(context['open_threads'], '$.context.open_threads')
        return errors

    keys = ['characters', 'references', 'locations', 'props', 'source_claims', 'beats', 'lines',
            'variants', 'pages', 'image_prompts', 'voices', 'shots', 'voice_scripts']
    maps = {key: indexed(data.get(key, []), '$.' + key) for key in keys}
    chars, refs, beats, lines, variants, pages = (maps[k] for k in (
        'characters', 'references', 'beats', 'lines', 'variants', 'pages'))
    panels = indexed([p for page in pages.values() for p in page['panels']], '$.panels')
    for key in ['beats', 'shots']:
        ordered(list(maps[key].values()), '$.' + key)
    for character in chars.values():
        names = [t['name'] for t in character['traits']]
        if len(names) != len(set(names)):
            err(character['id'], 'duplicate trait name')
    for reference in refs.values():
        rid = reference['id']
        if reference['availability'] == 'available' and (not reference['locator'] or not reference.get('availability_evidence')):
            err(rid, 'available reference requires locator and host availability evidence')
        if reference['inspection'] == 'inspected' and (
                reference['availability'] != 'available' or not reference.get('inspection_evidence')):
            err(rid, 'inspected requires available material and inspection evidence')
        if reference['role'] == 'character_identity':
            linked([reference['entity_id']], chars, rid)
        elif reference['role'] == 'style':
            if reference['entity_id'] != data.get('style', {}).get('id'):
                err(rid, 'unresolved style reference')
        elif reference['role'] in {'location', 'prop'}:
            linked([reference['entity_id']], maps['locations' if reference['role'] == 'location' else 'props'], rid)
    for beat in beats.values():
        linked(beat['source_claim_ids'], maps['source_claims'], beat['id'])
        if beat['interpretation'] == 'metaphor' and not beat.get('metaphor_limit'):
            err(beat['id'], 'metaphor requires a boundary of analogy')
    for line in lines.values():
        lid = line['id']
        if line['kind'] == 'dialogue':
            linked([line['speaker_id']], chars, lid)
        elif line['kind'] == 'narration' and line['speaker_id'] != 'narrator':
            err(lid, 'narration speaker must be narrator')
        elif line['kind'] == 'label' and line['speaker_id'] != 'none':
            err(lid, 'label speaker must be none')
        for field in ['on_image_text', 'spoken_text']:
            if line[field] is not None and line[field] != line['canonical_text']:
                if not any(v['field'] == field and v['approval'] == 'approved' for v in line['text_variants']):
                    err(lid, f'{field} differs without an approved text variant')
        if line['kind'] == 'label' and line['spoken_text'] is not None:
            err(lid, 'label is not automatically spoken; create an explicit narration line')

    for variant_id in variants:
        ordered([p for p in pages.values() if p['variant_id'] == variant_id], variant_id)
    coverage = {vid: {'beat': set(), 'line': set()} for vid in variants}
    for page in pages.values():
        pid, vid = page['id'], page['variant_id']
        linked([vid], variants, pid)
        local = indexed(page['panels'], pid)
        ordered(page['panels'], pid)
        if page['declared_panel_count'] != len(page['panels']):
            err(pid, 'declared panel count does not match described panels')
        if vid in variants and variants[vid]['format'] == 'single_panel' and len(local) != 1:
            err(pid, 'single_panel cannot contain a comic grid')
        allowed = set()
        for overlap in page['allowed_overlaps']:
            linked(overlap['panel_ids'], local, pid)
            allowed.add(frozenset(overlap['panel_ids']))
        for panel in page['panels']:
            rid = panel['id']
            rect(panel['rect'], rid)
            linked(panel['character_ids'], chars, rid)
            linked(panel['beat_ids'], beats, rid)
            if panel.get('location_id'):
                linked([panel['location_id']], maps['locations'], rid)
            linked(panel.get('prop_ids', []), maps['props'], rid)
            if vid in coverage:
                coverage[vid]['beat'].update(panel['beat_ids'])
            for zone in panel['text_zones']:
                rect(zone['rect'], rid + '.text_zone')
                linked([zone['line_id']], lines, rid)
                line = lines.get(zone['line_id'])
                if line and line['on_image_text'] is None:
                    err(rid, f'line {line["id"]} has no on-image text')
                if zone['kind'] == 'bubble':
                    if not line or line['kind'] != 'dialogue' or zone.get('tail_to') != line['speaker_id'] or zone.get('tail_to') not in panel['character_ids']:
                        err(rid, 'bubble must point to its actual speaker in this panel')
                elif zone.get('tail_to'):
                    err(rid, 'caption or label must not have a tail')
                if vid in coverage:
                    coverage[vid]['line'].add(zone['line_id'])
        values = list(local.values())
        for i, left in enumerate(values):
            for right in values[i + 1:]:
                x, y, w, h = left['rect']; a, b, c, d = right['rect']
                if min(x+w, a+c) > max(x, a)+1e-9 and min(y+h, b+d) > max(y, b)+1e-9:
                    if frozenset([left['id'], right['id']]) not in allowed:
                        err(pid, f'undeclared panel overlap {left["id"]} / {right["id"]}')
    if data['scope'] == 'full':
        for vid, variant in variants.items():
            for entity_type, wanted in [('beat', set(beats)), ('line', {
                    lid for lid, line in lines.items() if line['on_image_text'] is not None})]:
                omissions = {o['entity_id'] for o in variant['omissions'] if o['entity_type'] == entity_type}
                linked(omissions, beats if entity_type == 'beat' else lines, vid)
                for lost in wanted - coverage[vid][entity_type] - omissions:
                    err(vid, f'missing {entity_type} coverage {lost}')

    for prompt in maps['image_prompts'].values():
        pid = prompt['id']
        linked(prompt['reference_ids'], refs, pid)
        attached = [refs[r] for r in prompt['reference_ids'] if r in refs]
        if prompt['claims_attached'] and (not attached or any(r['availability'] != 'available' for r in attached)):
            err(pid, 'claims attached references that are missing or unchecked')
        if prompt['context_mode'] == 'with_context' and not prompt['required_context']:
            err(pid, 'with_context requires explicit accompanying materials')
        if prompt['readiness'] == 'ready' and any(r['availability'] != 'available' for r in attached):
            err(pid, 'ready prompt requires all declared reference inputs available')
        if prompt['context_mode'] == 'self_contained' and (not prompt['style_description'] or prompt['style_description'] not in prompt['text']):
            err(pid, 'self_contained prompt needs its compact style description in text')
        if prompt['target_type'] == 'character_reference':
            linked([prompt['target_id']], chars, pid)
            if prompt['panel_ids'] or prompt['declared_panel_count'] or prompt['visible_lines']:
                err(pid, 'character reference is not a comic page')
            continue
        linked([prompt.get('variant_id')], variants, pid)
        if prompt.get('variant_id') in variants and prompt['aspect_ratio'] != variants[prompt['variant_id']]['aspect_ratio']:
            err(pid, 'prompt aspect ratio differs from variant')
        if prompt['target_type'] == 'comic_page':
            linked([prompt['target_id']], pages, pid)
            page = pages.get(prompt['target_id'])
            target_panels = page['panels'] if page else []
            if page and page['variant_id'] != prompt.get('variant_id'):
                err(pid, 'target page belongs to another variant')
        else:
            linked([prompt['target_id']], panels, pid)
            target_panels = [panels[prompt['target_id']]] if prompt['target_id'] in panels else []
        target_ids = {p['id'] for p in target_panels}
        if set(prompt['panel_ids']) != target_ids or prompt['declared_panel_count'] != len(target_ids):
            err(pid, 'prompt panel declaration differs from target image unit')
        expected = {z['line_id'] for p in target_panels for z in p['text_zones']}
        actual = [v['line_id'] for v in prompt['visible_lines']]
        if set(actual) != expected or len(actual) != len(set(actual)):
            err(pid, 'missing or duplicated visible lines in image handoff')
        for visible in prompt['visible_lines']:
            linked([visible['line_id']], lines, pid)
            line = lines.get(visible['line_id'])
            if line and (visible['text'] != line['on_image_text'] or visible['speaker_id'] != line['speaker_id']):
                err(pid, f'visible line mismatch {visible["line_id"]}')
            if prompt['lettering_mode'] == 'in_image' and visible['text'] not in prompt['text']:
                err(pid, f'visible text absent from prompt: {visible["line_id"]}')

    for voice in maps['voices'].values():
        if voice['speaker_id'] != 'narrator':
            linked([voice['speaker_id']], chars, voice['id'])
    for shot in maps['shots'].values():
        linked([shot['variant_id']], variants, shot['id'])
        linked(shot['visual_ids'], {**pages, **panels}, shot['id'])
        if not shot['visual_ids']:
            err(shot['id'], 'shot requires at least one visual binding')
        for visual in shot['visual_ids']:
            owner = pages.get(visual) or next((p for p in pages.values() if any(q['id'] == visual for q in p['panels'])), None)
            if owner and owner['variant_id'] != shot['variant_id']:
                err(shot['id'], 'visual binding belongs to another variant')
        timing(shot['timing'], shot['id'])
    mode_kinds = {'silent': set(), 'narrator_only': {'narration'},
                  'narrator_and_characters': {'narration', 'dialogue'}, 'characters_only': {'dialogue'}}
    for script in maps['voice_scripts'].values():
        sid = script['id']
        indexed(script['events'], sid)
        ordered(script['events'], sid)
        selected = script['selected_line_ids']
        linked(selected, lines, sid)
        for lid in selected:
            line = lines.get(lid)
            if line and (line['kind'] not in mode_kinds[script['mode']] or line['spoken_text'] is None):
                err(sid, f'line {lid} cannot be spoken in {script["mode"]}')
        speech = [e for e in script['events'] if e['type'] == 'speech']
        if set(e.get('line_id') for e in speech) != set(selected):
            err(sid, 'chronological speech events differ from selected lines')
        seen_lines = Counter()
        for event in script['events']:
            eid = event['id']
            linked(event['shot_ids'], maps['shots'], eid)
            timing(event['timing'], eid)
            if event['type'] == 'speech':
                lid = event.get('line_id')
                linked([lid], lines, eid)
                linked([event.get('voice_profile_id')], maps['voices'], eid)
                if not event['shot_ids']:
                    err(eid, 'speech event requires a visual binding')
                voice = maps['voices'].get(event.get('voice_profile_id'))
                if voice and lid in lines and voice['speaker_id'] != lines[lid]['speaker_id']:
                    err(eid, 'voice profile belongs to another speaker')
                if seen_lines[lid] and not (event.get('repeat') and event.get('repeat_reason')):
                    err(eid, f'unintended repeated speech {lid}')
                seen_lines[lid] += 1
            elif 'line_id' in event or 'voice_profile_id' in event:
                err(eid, 'pause does not contain a spoken line or voice')
        intervals = [e for e in speech if e['timing']['kind'] == 'externally_supplied']
        for i, left in enumerate(intervals):
            for right in intervals[i+1:]:
                if min(left['timing']['end'], right['timing']['end']) > max(left['timing']['start'], right['timing']['start']):
                    if not (left['allow_overlap'] and right['allow_overlap']):
                        err(sid, f'undeclared speech overlap {left["id"]} / {right["id"]}')
    for i, value in enumerate(data.get('reviews', [])):
        review(value, f'$.reviews[{i}]')
    context = data.get('project_context')
    if context:
        context_chars = indexed(context['characters'], '$.project_context.characters')
        indexed(context['voices'], '$.project_context.voices')
        indexed(context['episode_summaries'], '$.project_context.episode_summaries')
        indexed(context['open_threads'], '$.project_context.open_threads')
        for voice in context['voices']:
            if voice['speaker_id'] != 'narrator':
                linked([voice['speaker_id']], context_chars, '$.project_context.voices')
    delta = data.get('context_delta')
    if delta:
        if not context or delta['base_revision'] != context['context_revision']:
            err('$.context_delta', 'stale or unknown base context revision')
        for change in delta['changes']:
            mapping = {'character': {c['id']: c for c in context['characters']} if context else {},
                       'style': {context['style']['id']: context['style']} if context else {},
                       'line': lines,
                       'episode_summary': {x['id']: x for x in context['episode_summaries']} if context else {},
                       'open_thread': {x['id']: x for x in context['open_threads']} if context else {}}[change['entity_type']]
            old = mapping.get(change['entity_id'])
            if old is None:
                if change['entity_type'] not in {'episode_summary', 'open_thread'} or change['old_value'] is not None:
                    err('$.context_delta', f'unresolved revision target {change["entity_id"]}')
            elif change['field'] not in old or old[change['field']] != change['old_value']:
                err('$.context_delta', f'old_value mismatch for {change["entity_id"]}.{change["field"]}')
    revision = data.get('targeted_revision')
    if revision and revision['base_document_revision'] != data.get('previous_document_revision'):
        err('$.targeted_revision', 'stale or unknown base document revision')
    if revision:
        current_groups = {'line': lines, 'character': chars,
                          'style': {data['style']['id']: data['style']} if data.get('style') else {}}
        for change in revision['changes']:
            target = current_groups.get(change['entity_type'], {}).get(change['entity_id'])
            if target is None or change['field'] not in target:
                err('$.targeted_revision', f'unresolved revision target/field {change["entity_id"]}.{change["field"]}')
            elif change['field'] in {'id', 'project_id', 'origin_project_id'}:
                err('$.targeted_revision', 'revision must preserve identity and ownership fields')
            elif target[change['field']] != change['new_value']:
                err('$.targeted_revision', f'new_value mismatch for {change["entity_id"]}.{change["field"]}')
    if data['readiness'] == 'ready' and data['missing_inputs']:
        err('$.readiness', 'ready text package still declares missing required inputs')
    return errors


def validate(data, baseline=None):
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:
        raise RuntimeError('Install pinned validation dependencies from requirements-checks.txt') from error
    if not isinstance(data, dict) or data.get('kind') not in SCHEMAS:
        return {'status': 'failed', 'errors': [{'path': '$.kind', 'message': 'unsupported document kind'}], 'warnings': []}
    schema = load_json(ROOT / 'schemas' / (SCHEMAS[data['kind']] + '.schema.json'))
    Draft202012Validator.check_schema(schema)
    structural = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(list(e.path)))
    errors = [{'path': '$' + ''.join(f'[{p}]' if isinstance(p, int) else '.' + p for p in e.path),
               'message': e.message} for e in structural]
    if not errors:
        errors = semantic_errors(data)
    warnings = []
    revision = data.get('targeted_revision')
    if not errors and revision:
        if baseline is None:
            warnings.append('Targeted revision old_value comparison skipped: no --baseline document supplied.')
        elif any(data.get(k) != baseline.get(k) for k in ('project_id', 'episode_id')) or revision['base_document_revision'] != baseline.get('document_revision'):
            errors.append({'path': '$.targeted_revision', 'message': 'baseline project/episode/revision mismatch'})
        else:
            groups = {'line': baseline.get('lines', []), 'character': baseline.get('characters', []),
                      'style': [baseline['style']] if baseline.get('style') else []}
            for change in revision['changes']:
                target = next((x for x in groups.get(change['entity_type'], []) if x['id'] == change['entity_id']), None)
                if target is None or target.get(change['field']) != change['old_value']:
                    errors.append({'path': '$.targeted_revision', 'message': f'old_value mismatch for {change["entity_id"]}'})
    return {'status': 'failed' if errors else 'passed', 'errors': errors, 'warnings': warnings,
            'checked': ['JSON Schema Draft 2020-12', 'declared semantic rules']}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--baseline', help='Optional previous package for old_value comparison.')
    args = parser.parse_args()
    try:
        data = load_json(args.input)
    except (ValueError, UnicodeError) as error:
        result = {'status': 'failed', 'errors': [{'path': '$', 'message': str(error)}]}
        code = 1
    except OSError as error:
        result = {'status': 'failed', 'execution_error': str(error)}
        code = 2
    else:
        try:
            baseline = load_json(args.baseline) if args.baseline else None
            result = validate(data, baseline=baseline)
            code = 0 if result['status'] == 'passed' else 1
        except Exception as error:
            result = {'status': 'failed', 'execution_error': f'{type(error).__name__}: {error}'}
            code = 2
    print(json.dumps(result, ensure_ascii=False) if args.json else json.dumps(result, ensure_ascii=False, indent=2))
    if code == 2:
        print(result['execution_error'], file=sys.stderr)
    return code


if __name__ == '__main__':
    sys.exit(main())
