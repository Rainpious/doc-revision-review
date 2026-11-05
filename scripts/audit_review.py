"""Read-only coverage, DOCX comment reconciliation and semantic review triage.

Flags are review candidates, never automatic pairing or technical acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET

RUNTIME = Path(__file__).resolve().parent / 'runtime'
sys.path.insert(0, str(RUNTIME))
from revision_tool import validate

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_input(path):
    raw = Path(path).read_text(encoding='utf-8')
    if Path(path).suffix.lower() == '.html':
        match = re.search(r'<script\b[^>]*\bid=["\']revision-package["\'][^>]*>(.*?)</script>', raw, re.S)
        if not match:
            raise ValueError('HTML has no revision-package')
        return validate(json.loads(match.group(1))), raw
    return validate(json.loads(raw)), None


def verify_runtime():
    release = json.loads((RUNTIME / 'release.json').read_text(encoding='utf-8'))
    for f in release['files']:
        if sha(RUNTIME / f['path']) != f['sha256']:
            raise ValueError('Pinned runtime differs from release manifest: ' + f['path'])
    return release


def check_assets(html):
    release = verify_runtime()
    expected = {
        'app-code': '\n'.join((RUNTIME / 'viewer' / f).read_text(encoding='utf-8') for f in ('engine.js', 'app.js')),
        'app-style': (RUNTIME / 'viewer/app.css').read_text(encoding='utf-8'),
    }
    matches = {}
    for key, wanted in expected.items():
        tag = 'script' if key == 'app-code' else 'style'
        m = re.search(r'<' + tag + r'\b[^>]*\bid=["\']' + key + r'["\'][^>]*>(.*?)</' + tag + '>', html, re.S)
        matches[key] = bool(m and m.group(1) == wanted)
    return dict(expected_release=release['release'], local_manifest_valid=True,
                embedded_assets_match=all(matches.values()), assets=matches)


def block_text(b):
    c = b['content']
    if c['type'] == 'text':
        return c['text']
    if c['type'] == 'table':
        return '\n'.join('\t'.join(row) for row in [c['headers'], *c['rows']])
    return c.get('caption', '')


def brief(b):
    return dict(id=b['id'], kind=b['kind'], order=b['order'], source_locator=b['source_locator'],
                excerpt=block_text(b)[:240])


def normalize(text):
    return ''.join(text.split())


def docx_comments(path, document, blocks):
    """Keep every comment (including table/story anchors), without assigning expertise."""
    anchors = defaultdict(set)
    with ZipFile(path) as z:
        if 'word/comments.xml' not in z.namelist():
            return []
        for part in z.namelist():
            if not re.fullmatch(r'word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml', part):
                continue
            root = ET.fromstring(z.read(part))
            body = root.find(W + 'body') if part == 'word/document.xml' else None
            if body is None:
                groups = [(part, root)]
            else:
                groups = [(f'{part} / body[{i}]', el) for i, el in enumerate(body, 1)]
            for locator, group in groups:
                for el in group.iter():
                    if el.tag in (W + 'commentRangeStart', W + 'commentRangeEnd', W + 'commentReference'):
                        anchors[el.get(W + 'id')].add(locator)
        comments = []
        root = ET.fromstring(z.read('word/comments.xml'))
        for c in root.findall(W + 'comment'):
            cid = c.get(W + 'id')
            paragraphs = [''.join(t.text or '' for t in p.iter(W + 't')) for p in c.iter(W + 'p')]
            locators = sorted(anchors[cid])
            candidates = [b['id'] for b in blocks if any(
                b['source_locator'] == loc or b['source_locator'].startswith(loc + ' /') for loc in locators)]
            comments.append(dict(document_id=document['id'], source_comment_id=cid,
                author=c.get(W + 'author'), date=c.get(W + 'date'),
                quote='\n'.join(paragraphs), source_part='word/comments.xml',
                anchor_locators=locators, candidate_block_ids=candidates))
        return comments


def reconcile_comments(comments, opinions, docid):
    # Multiset accounting: identical quotes in two source comments need two entries.
    available = defaultdict(deque)
    for opinion in opinions:
        if opinion['target']['document_id'] == docid:
            available[normalize(opinion['quote'])].append(opinion)
    result = []
    for c in comments:
        queue = available[normalize(c['quote'])]
        match = queue.popleft() if queue else None
        result.append(dict(c, imported_opinion_id=match['id'] if match else None,
                           imported_target_block_ids=match['target']['block_ids'] if match else []))
    return result


def audit(data, sources=None, html=None, low_similarity=0.30):
    if not 0 <= low_similarity <= 1:
        raise ValueError('low_similarity must be between 0 and 1')
    sources = sources or {}
    bm = {b['id']: b for b in data['blocks']}
    used = {bid for r in data['relations'] for bid in r['from_ids'] + r['to_ids']}
    changed = [r for r in data['relations'] if r['content_changed'] or r['moved'] or r['kind'] != 'correspondence']
    changed_ids = {bid for r in changed for bid in r['from_ids'] + r['to_ids']}
    result = dict(schema_version=1, package_id=data['package_id'], review_revision=data['review_revision'],
        state_hash=data['working_copy']['state_hash'], sources={},
        relation_kinds=dict(Counter(r['kind'] for r in data['relations'])),
        changed_relations=len(changed), semantic_candidates=[],
        annotation_counts={k: len(data[k]) for k in ('changes', 'reasons', 'opinions', 'decisions')},
        runtime=check_assets(html) if html is not None else None,
        note='Triage only. Counts and similarity cannot certify semantic correctness or professional acceptance.')
    for doc in data['source_documents']:
        role, docid = doc['role'], doc['id']
        blocks = [b for b in data['blocks'] if b['document_id'] == docid]
        preview = data.get('page_previews', {}).get(docid)
        kinds = {}
        for kind in sorted({b['kind'] for b in blocks}):
            members = [b for b in blocks if b['kind'] == kind]
            kinds[kind] = dict(extracted=len(members), paired=sum(b['id'] in used for b in members),
                changed=sum(b['id'] in changed_ids for b in members),
                page_located=sum(bool(preview['locations'].get(b['id'], {}).get('boxes')) for b in members) if preview else None,
                glyph_mapped=sum(bool(preview.get('glyphs', {}).get(b['id'])) for b in members if b['content']['type'] == 'text') if preview else None)
        unmatched = [brief(b) for b in blocks if b['id'] not in used]
        comments = dict(status='not_checked', reason='Source file not supplied; zero comments is not inferred.')
        source = sources.get(role)
        identity = dict(status='not_checked')
        if source:
            source = Path(source).resolve()
            digest = sha(source)
            if digest != doc['sha256'] or doc['identity_kind'] != 'file_bytes':
                raise ValueError(f'{role} source identity does not match package: {source}')
            identity = dict(status='verified', path=str(source), sha256=digest)
            if source.suffix.lower() == '.docx':
                inventory = reconcile_comments(docx_comments(source, doc, blocks), data['opinions'], docid)
                missing = [c for c in inventory if c['imported_opinion_id'] is None]
                comments = dict(status='checked', source_count=len(inventory),
                    imported_count=len(inventory)-len(missing), missing_count=len(missing), inventory=inventory)
            elif source.suffix.lower() == '.pdf':
                comments = dict(status='not_supported', reason='PDF annotation import is not supported; inspect source separately.')
            else:
                raise ValueError('Expected DOCX or PDF source')
            if sha(source) != digest:
                raise ValueError('Source changed during audit: ' + str(source))
        result['sources'][role] = dict(document_id=docid, identity=identity, by_kind=kinds,
            unmatched_count=len(unmatched), unmatched=unmatched,
            source_coverage=doc['coverage'], comments=comments)
    for r in data['relations']:
        hints = []
        if r['kind'] in ('split', 'merge', 'reorganization') or r['moved']:
            hints.append('structural_relation')
        if r['confidence'] < 70:
            hints.append('low_recorded_confidence')
        similarity = None
        if r['from_ids'] and r['to_ids']:
            old = [bm[x] for x in r['from_ids']]
            new = [bm[x] for x in r['to_ids']]
            if {b['kind'] for b in old} != {b['kind'] for b in new}:
                hints.append('cross_kind_pair')
            if all(b['content']['type'] == 'text' for b in old + new):
                a, b = '\n'.join(block_text(x) for x in old), '\n'.join(block_text(x) for x in new)
                similarity = SequenceMatcher(None, a, b, autojunk=False).ratio()
                if similarity < low_similarity:
                    hints.append('low_text_similarity')
        if hints:
            result['semantic_candidates'].append(dict(relation_id=r['id'], hints=hints,
                recorded_confidence=r['confidence'], method=r['method'], review_status=r['review_status'],
                similarity=similarity, old=[brief(bm[x]) for x in r['from_ids']], new=[brief(bm[x]) for x in r['to_ids']]))
    triggers = []
    if any(s['unmatched_count'] for s in result['sources'].values()):
        triggers.append('unmatched_blocks')
    if result['semantic_candidates']:
        triggers.append('semantic_pairing_candidates')
    if any(s['comments'].get('missing_count', 0) for s in result['sources'].values()):
        triggers.append('source_comments_missing_from_html')
    if any(s['comments']['status'] == 'not_checked' for s in result['sources'].values()):
        triggers.append('source_comment_coverage_not_checked')
    if result['runtime'] and not result['runtime']['embedded_assets_match']:
        triggers.append('runtime_assets_differ_from_current_release')
    result['triage'] = dict(route='review_required' if triggers else 'standard_review', triggers=triggers,
        low_similarity_heuristic=low_similarity, semantic_acceptance='not_assessed',
        delivery_complete=False,
        rule='Resolve or disclose material gaps. Standard route also requires task-specific reading; no score grants acceptance.')
    return result


def save_audit(result, out):
    out = Path(out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path, help='Package JSON or offline HTML')
    p.add_argument('--old', type=Path); p.add_argument('--new', type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--low-similarity', type=float, default=.30, help='Triage heuristic, not acceptance threshold')
    a = p.parse_args()
    if a.out.exists():
        p.error('Audit output already exists; choose a new filename')
    data, html = read_input(a.input)
    report = audit(data, {role: path for role, path in [('old', a.old), ('new', a.new)] if path}, html, a.low_similarity)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    save_audit(report, a.out)
    print(json.dumps(dict(audit=str(a.out), triage=report['triage']), ensure_ascii=False))


if __name__ == '__main__':
    main()
