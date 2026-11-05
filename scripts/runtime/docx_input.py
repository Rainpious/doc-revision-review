"""Read-only DOCX extraction and conservative candidate matching.

No professional reasons, impact conclusions or expert identities are inferred.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import io
import json
from pathlib import Path
import posixpath
from uuid import uuid4
from zipfile import ZipFile
from datetime import datetime, timezone

from lxml import etree
from PIL import Image

from revision_tool import cardinality, canonical, seal, save, validate

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'v': 'urn:schemas-microsoft-com:vml',
      'w14': 'http://schemas.microsoft.com/office/word/2010/wordml'}


def xml(raw):
    return etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))


def q(local):
    return '{' + NS['w'] + '}' + local


def extract(path, role, version):
    path = Path(path)
    docid = 'doc-' + role
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    blocks, omissions, notes = [], [], []
    counts = Counter()
    occurrences = Counter()
    with ZipFile(path) as z:
        main = xml(z.read('word/document.xml'))
        style_names = {}
        if 'word/styles.xml' in z.namelist():
            for style in xml(z.read('word/styles.xml')).findall('w:style', NS):
                name = style.find('w:name', NS)
                if name is not None:
                    style_names[style.get(q('styleId'))] = name.get(q('val'), '')
        rels = {}
        if 'word/_rels/document.xml.rels' in z.namelist():
            for r in xml(z.read('word/_rels/document.xml.rels')):
                if r.get('TargetMode') != 'External':
                    rels[r.get('Id')] = posixpath.normpath(posixpath.join('word', r.get('Target')))
        if main.xpath('//w:ins|//w:del', namespaces=NS):
            notes.append('正文含修订：提取显示插入、排除删除后的最终文本，不自动接受/修改源文件。')
        rich_count = 0

        def add(kind, content, locator, editable=False, stable=None):
            # Stable content identity within the input; repeated text gets an occurrence suffix.
            token = stable or hashlib.sha256(canonical([kind, content]).encode('utf-8')).hexdigest()[:16]
            occurrences[token] += 1
            bid = f'{role}-{token}-{occurrences[token]}'
            blocks.append(dict(id=bid, document_id=docid, order=len(blocks), kind=kind,
                content=content, source_locator=locator, version=1, editable=editable))
            counts[kind] += 1
            return bid

        def deleted(el):
            return any(a.tag == q('del') for a in el.iterancestors())

        def paragraph_text(el):
            rich = False
            parts = []
            for r in el.iter(q('r')):
                if deleted(r):
                    continue
                txt = ''.join(t.text or '' if t.tag == q('t') else '\t' if t.tag == q('tab') else '\n'
                              for t in r if t.tag in (q('t'), q('tab'), q('br'), q('cr')))
                vertical = r.find('w:rPr/w:vertAlign', NS)
                if vertical is not None and txt:
                    val = vertical.get(q('val'))
                    table = str.maketrans('0123456789+-=()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾' if val == 'superscript' else '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎')
                    txt = txt.translate(table) if all(c in '0123456789+-=()' for c in txt) else (f'^{{{txt}}}' if val == 'superscript' else f'_{{{txt}}}')
                    rich = True
                parts.append(txt)
            return ''.join(parts), rich

        section = ''
        for index, el in enumerate(main.find('w:body', NS)):
            locator = f'word/document.xml / body[{index + 1}]'
            if el.tag == q('p'):
                text, rich = paragraph_text(el)
                rich_count += rich
                style_el = el.find('w:pPr/w:pStyle', NS)
                style = style_el.get(q('val'), '') if style_el is not None else ''
                name = style_names.get(style, style)
                kind = 'heading' if ('heading' in name.lower() or name.startswith('标题') or el.find('w:pPr/w:outlineLvl', NS) is not None) else 'list_item' if el.find('w:pPr/w:numPr', NS) is not None else 'paragraph'
                if kind == 'heading':
                    section = text
                if text:
                    add(kind, dict(type='text', text=text), locator + (f' / {section}' if section else ''),
                        editable=role == 'new' and kind in ('paragraph', 'list_item') and not rich,
                        stable=el.get('{' + NS['w14'] + '}paraId'))
                refs = el.xpath('.//a:blip/@r:embed|.//v:imagedata/@r:id', namespaces=NS)
                for j, rid in enumerate(refs):
                    target = rels.get(rid)
                    try:
                        if not target:
                            raise ValueError('missing image relationship')
                        raw = z.read(target)
                        with Image.open(io.BytesIO(raw)) as image:
                            image.load()
                            output = io.BytesIO()
                            image.convert('RGBA').save(output, format='PNG')
                        captions = el.xpath('.//*[local-name()="docPr"]/@descr')
                        add('image', dict(type='image', data_url='data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(),
                            alt=captions[0] if captions else Path(target).name, caption='来源图件：' + Path(target).name), locator + f' / image[{j+1}]')
                    except (ValueError, KeyError, OSError) as exc:
                        omissions.append(f'{locator} 图件 {rid} 无法解码：{type(exc).__name__}')
                        add('caption', dict(type='text', text=f'〔图件未提取：{rid}，请核对原DOCX〕'), locator)
                if el.xpath('.//a:blip[@r:link]', namespaces=NS):
                    omissions.append(locator + ' 外链图像未下载，须在源文件核对')
                if el.xpath('.//w:txbxContent|.//w:object|.//*[local-name()="oMath"]|.//*[local-name()="chart"]', namespaces=NS):
                    omissions.append(locator + ' 含文本框/对象/公式/图表，未承诺恢复其布局与语义，须核对原件')
                if el.xpath('.//w:numPr', namespaces=NS):
                    notes.append('列表编号由Word自动生成，当前保留列表项文字，不恢复自动编号标签。')
            elif el.tag == q('tbl'):
                rows = []
                complex_table = False
                for row in el.findall('w:tr', NS):
                    values = []
                    for cell in row.findall('w:tc', NS):
                        values.append('\n'.join(paragraph_text(p)[0] for p in cell.findall('w:p', NS)))
                        if cell.find('w:tcPr/w:gridSpan', NS) is not None or cell.find('w:tcPr/w:vMerge', NS) is not None or cell.find('w:tbl', NS) is not None:
                            complex_table = True
                    rows.append(values)
                if not rows:
                    continue
                width = max(map(len, rows))
                if complex_table or any(len(r) != width for r in rows):
                    omissions.append(locator + ' 复杂/合并表格转为物理单元格矩形展示，合并关系/嵌套结构未恢复')
                rows = [r + [''] * (width - len(r)) for r in rows]
                # First physical row is used as header only when explicitly marked.
                first = el.find('w:tr', NS)
                has_header = first.find('w:trPr/w:tblHeader', NS) is not None
                headers = rows[0] if has_header else [''] * width
                add('table', dict(type='table', headers=headers, rows=rows[1:] if has_header else rows), locator)
                if el.xpath('.//a:blip|.//w:object|.//*[local-name()="oMath"]', namespaces=NS):
                    omissions.append(locator + ' 表内图像/对象/公式未提取，须核对原DOCX')
            elif el.tag not in (q('sectPr'),):
                omissions.append(locator + ' 未支持的正文结构 ' + etree.QName(el).localname)
        for name in z.namelist():
            if name.startswith('word/') and any(Path(name).name.startswith(x) for x in ('header', 'footer', 'footnotes', 'endnotes', 'comments')) and name.endswith('.xml'):
                root = xml(z.read(name))
                if root.xpath('//w:t', namespaces=NS):
                    omissions.append(name + ' 的页眉/页脚/脚注/尾注/批注内容未进入正文比较，需原件核对')
        if rich_count:
            notes.append(f'{rich_count} 个段落的上下标已保留为Unicode或显式标记，禁止浏览器编辑以免丢失语义。')
        notes.append('不恢复原分页、版式与自动域；定位使用源XML位置。图件转PNG仅用于阅读，不做像素差异结论。')
    document = dict(id=docid, role=role, filename=path.name, report_version=version, sha256=digest,
        identity_kind='file_bytes', coverage=dict(status='partial' if omissions else 'complete_supported',
            omissions=list(dict.fromkeys(omissions)), notes='；'.join(dict.fromkeys(notes))))
    return document, blocks, dict(counts)


def match(old, new):
    """Unique exact matches, contiguous split/merge candidates, then mutual best.

    Similarity matches remain unreviewed. Ambiguous short text stays unmatched.
    """
    relations, used_old, used_new = [], set(), set()
    def fingerprint(b):
        return canonical([b['kind'], b['content']])
    def pair(a, b, method, confidence, changed):
        relations.append(dict(id='r-' + hashlib.sha256(canonical([[x['id'] for x in a], [x['id'] for x in b]]).encode()).hexdigest()[:16],
            from_ids=[x['id'] for x in a], to_ids=[x['id'] for x in b], kind=cardinality(a,b),
            moved=False, content_changed=changed, method=method, confidence=confidence,
            review_status='unreviewed', diff_status='not_computed'))
        used_old.update(x['id'] for x in a);used_new.update(x['id'] for x in b)
    aindex, bindex = defaultdict(list), defaultdict(list)
    for b in old:aindex[fingerprint(b)].append(b)
    for b in new:bindex[fingerprint(b)].append(b)
    for key in sorted(aindex.keys() & bindex.keys()):
        if len(aindex[key]) == len(bindex[key]) == 1:
            pair(aindex[key], bindex[key], 'similarity_candidate', 100, False)
    # Word paraId is an input stable key, never a heading number or paragraph index.
    # Candidates remain unreviewed, including wholesale rewrites at that key.
    import re
    stable_old={b['id'].split('-')[1]:b for b in old if re.fullmatch(r'old-[A-Fa-f0-9]{8}-1',b['id'])}
    stable_new={b['id'].split('-')[1]:b for b in new if re.fullmatch(r'new-[A-Fa-f0-9]{8}-1',b['id'])}
    for key in sorted(stable_old.keys() & stable_new.keys()):
        a,b=stable_old[key],stable_new[key]
        if a['id'] not in used_old and b['id'] not in used_new and a['kind']==b['kind']:
            score=SequenceMatcher(None,a['content'].get('text',''),b['content'].get('text',''),autojunk=False).ratio()
            pair([a],[b],'stable_id',round(score*100),a['content']!=b['content'])
    # Concatenated exact text supports split/merge without claiming semantic inference.
    def norm(s):return ''.join(s.split())
    for left,right,swap in ((old,new,False),(new,old,True)):
        for b in left:
            used_left, used_right = (used_new,used_old) if swap else (used_old,used_new)
            if b['id'] in used_left or b['content']['type']!='text':continue
            candidates=[]
            for start in range(len(right)):
                for size in (2,3,4):
                    group=right[start:start+size]
                    if len(group)!=size or any(x['id'] in used_right or x['content']['type']!='text' or x['kind']=='heading' for x in group):continue
                    if norm(b['content']['text'])==norm(''.join(x['content']['text'] for x in group)):
                        candidates.append(group)
            if len(candidates)==1:
                pair(candidates[0] if swap else [b], [b] if swap else candidates[0], 'similarity_candidate', 95, True)
    remaining_old=[b for b in old if b['id'] not in used_old and b['content']['type']=='text']
    remaining_new=[b for b in new if b['id'] not in used_new and b['content']['type']=='text']
    scores={}
    for a in remaining_old:
        for b in remaining_new:
            if a['kind']!=b['kind']:continue
            x,y=a['content']['text'],b['content']['text']
            if min(len(x),len(y))<12:continue
            score=SequenceMatcher(None,x,y,autojunk=False).ratio()
            if score>=0.55:scores[a['id'],b['id']]=score
    for a in remaining_old:
        ranked=sorted(((v,bid) for (aid,bid),v in scores.items() if aid==a['id']),reverse=True)
        if not ranked:continue
        score,bid=ranked[0]
        reverse=sorted(((v,aid) for (aid,x),v in scores.items() if x==bid),reverse=True)
        if reverse[0][1]!=a['id'] or (len(ranked)>1 and score-ranked[1][0]<0.08) or (len(reverse)>1 and score-reverse[1][0]<0.08):continue
        b=next(b for b in remaining_new if b['id']==bid)
        if a['id'] not in used_old and bid not in used_new:pair([a],[b],'similarity_candidate',round(score*100),True)
    # Movement uses inversion of known paired order, not ordinal differences due to insertions.
    ordered=sorted([r for r in relations if len(r['from_ids'])==len(r['to_ids'])==1],key=lambda r:next(b['order'] for b in old if b['id']==r['from_ids'][0]))
    positions=[next(b['order'] for b in new if b['id']==r['to_ids'][0]) for r in ordered]
    # Longest increasing subsequence supplies a stable backbone. Others are movement candidates.
    lengths=[1]*len(positions);prev=[-1]*len(positions)
    for i in range(len(positions)):
        for j in range(i):
            if positions[j]<positions[i] and lengths[j]+1>lengths[i]:lengths[i]=lengths[j]+1;prev[i]=j
    backbone=set()
    if positions:
        i=max(range(len(positions)),key=lambda i:lengths[i])
        while i>=0:backbone.add(i);i=prev[i]
    for i,r in enumerate(ordered):r['moved']=i not in backbone
    return sorted(relations,key=lambda r:min((b['order'] for b in new if b['id'] in r['to_ids']),default=10**9))


def compare(oldpath,newpath,old_version,new_version,title=None):
    od,old,oc=extract(oldpath,'old',old_version);nd,new,nc=extract(newpath,'new',new_version)
    relations=match(old,new)
    result=dict(schema_version='1.0',package_id='review-'+uuid4().hex,title=title or f'{Path(newpath).stem} · 修订对照',
        review_revision=0,parent_revision=None,generated_at=datetime.now(timezone.utc).isoformat(),
        source_documents=[od,nd],blocks=old+new,relations=relations,reasons=[],changes=[],opinions=[],decisions=[],
        summary=dict(text='自动提取与候选匹配已完成；判断影响、修改原因及专家意见尚未人工登记。未配对内容不自动视为新增/删除，请核对来源覆盖与对应关系。',change_ids=[],review_status='unreviewed'),
        working_copy=dict(overrides={},state_hash='0'*64,report_sync='source_baseline'),operations=[],relation_history=[])
    return validate(seal(result)),dict(old=oc,new=nc,relations=len(relations),unmatched_old=len(old)-sum(len(r['from_ids']) for r in relations),unmatched_new=len(new)-sum(len(r['to_ids']) for r in relations))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('old',type=Path);p.add_argument('new',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--old-version',required=True);p.add_argument('--new-version',required=True);p.add_argument('--title')
    args=p.parse_args()
    result,stats=compare(args.old,args.new,args.old_version,args.new_version,args.title)
    save(args.output,result);print(json.dumps(stats,ensure_ascii=False))
