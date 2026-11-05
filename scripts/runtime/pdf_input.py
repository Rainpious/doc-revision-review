"""Text PDF comparison and source-page previews. No OCR or pixel-diff claims."""
from __future__ import annotations
import argparse
import base64
from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
import hashlib
import io
import unicodedata
from pathlib import Path
from uuid import uuid4
import pymupdf
from PIL import Image
from docx_input import match
from revision_tool import canonical, load, save, seal, validate, ContractError


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text) if not c.isspace())


def coords(page_number,rect,page):
    rect=pymupdf.Rect(rect) & page.rect
    x=max(0,round(rect.x0/page.rect.width*100000));y=max(0,round(rect.y0/page.rect.height*100000))
    w=min(100000-x,max(1,round(rect.width/page.rect.width*100000)));h=min(100000-y,max(1,round(rect.height/page.rect.height*100000)))
    return dict(page=page_number,x=x,y=y,w=w,h=h)


def pixel_hash(image):
    im=image.convert('RGB')
    return (im.size,hashlib.sha256(im.tobytes()).hexdigest())


def preview(pdf_path,document,blocks):
    path=Path(pdf_path);pages=[];stream=[];line_refs=[];page_sizes={};images=defaultdict(list)
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:raise ContractError('Encrypted PDF must be unlocked by its owner before reading')
        for number,page in enumerate(pdf,1):
            page_sizes[number]=page.rect
            pix=page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),alpha=False)
            pages.append(dict(number=number,width=round(page.rect.width),height=round(page.rect.height),data_url='data:image/jpeg;base64,'+base64.b64encode(pix.tobytes('jpeg',jpg_quality=85)).decode()))
            for block in page.get_text('dict',sort=True)['blocks']:
                if block['type']==0:
                    for line in block['lines']:
                        text=norm(''.join(span['text'] for span in line['spans']))
                        stream.append(text);line_refs.extend([(number,tuple(line['bbox']))]*len(text))
            for info in page.get_images(full=True):
                try:
                    image=pdf.extract_image(info[0])
                    with Image.open(io.BytesIO(image['image'])) as im:key=pixel_hash(im)
                    for rect in page.get_image_rects(info[0]):images[key].append(coords(number,rect,page))
                except (ValueError,OSError):pass
        text=''.join(stream);locations={};unmapped=[];offsets={};candidates={}
        for block in blocks:
            content=block['content'];boxes=[];method='exact_text'
            if content['type']=='image':
                raw=base64.b64decode(content['data_url'].split(',',1)[1])
                with Image.open(io.BytesIO(raw)) as im:matches=images.get(pixel_hash(im),[])
                if len(matches)==1:boxes=matches;method='image_pixels'
            else:
                value=content['text'] if content['type']=='text' else ''.join(content['headers']+sum(content['rows'],[]))
                target=norm(value);start=text.find(target) if target else -1
                if start>=0 and text.find(target,start+1)<0:
                    refs=line_refs[start:start+len(target)]
                    offsets[block['id']]=(start,start+len(target))
                else:
                    if start>=0:
                        candidates[block['id']]=[];hit=start
                        while hit>=0:
                            candidates[block['id']].append((hit,hit+len(target)))
                            hit=text.find(target,hit+1)
                    # A unique long prefix/suffix identifies a location, never full paragraph bounds.
                    anchors=[target[:48],target[-48:]] if len(target)>=48 else []
                    refs=[]
                    for anchor in anchors:
                        hit=text.find(anchor)
                        if hit>=0 and text.find(anchor,hit+1)<0:
                            refs.extend(line_refs[hit:hit+len(anchor)])
                            offsets.setdefault(block['id'],(hit,hit+len(anchor)))
                    if refs:method='partial_anchor'
                for page_number,rect in dict.fromkeys(refs):
                    page=pdf[page_number-1];boxes.append(coords(page_number,rect,page))
            if boxes:locations[block['id']]=dict(method=method,boxes=boxes)
            else:unmapped.append(block['id'])
        # Repeated titles/TOC entries need surrounding unique text, not a blind first hit.
        for index,block in enumerate(blocks):
            bid=block['id']
            if bid in locations or bid not in candidates:continue
            before=next((offsets[x['id']][1] for x in reversed(blocks[:index]) if x['id'] in offsets),0)
            after=next((offsets[x['id']][0] for x in blocks[index+1:] if x['id'] in offsets),len(text))
            hits=[pair for pair in candidates[bid] if pair[0]>=before and pair[1]<=after]
            if len(hits)==1:
                start,end=hits[0];offsets[bid]=(start,end)
                boxes=[coords(n,rect,pdf[n-1]) for n,rect in dict.fromkeys(line_refs[start:end])]
                if boxes:locations[bid]=dict(method='exact_text',boxes=boxes);unmapped.remove(bid)
    from pdf_glyphs import glyph_map
    glyphs=glyph_map(path,blocks,locations)
    return dict(glyphs=glyphs,pdf_filename=path.name,pdf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_sha256=document['sha256'],
        pages=pages,locations=locations,unmapped_ids=unmapped,
        notes=['原PDF页面渲染为内嵌阅读图像；不做像素差异或自动版式判断。',
               '精确文字匹配与唯一图像像素匹配用于定位；部分锚点仅标示可确认文字所在区域。',
               '审阅文字编辑不会改变本PDF原页；需制作端重建正式报告与预览。'])


def attach(data,old_pdf,new_pdf):
    validate(data);out=copy.deepcopy(data);out['page_previews']={}
    for role,path in [('old',old_pdf),('new',new_pdf)]:
        document=next(d for d in out['source_documents'] if d['role']==role)
        out['page_previews'][document['id']]=preview(path,document,[b for b in out['blocks'] if b['document_id']==document['id']])
    return validate(seal(out))


def extract_pdf(path,role,version):
    path=Path(path);blocks=[];occurrences=Counter();notes=[]
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:raise ContractError('Encrypted PDF is not supported')
        for number,page in enumerate(pdf,1):
            if not page.get_text().strip():notes.append(f'第{number}页无可提取文字：可能为扫描/纯图页，未执行OCR')
            for item in page.get_text('dict',sort=True)['blocks']:
                if item['type']!=0:continue
                text='\n'.join(''.join(s['text'] for s in line['spans']) for line in item['lines']).strip()
                if not text:continue
                token=hashlib.sha256(text.encode()).hexdigest()[:16];occurrences[token]+=1
                blocks.append(dict(id=f'{role}-{token}-{occurrences[token]}',document_id='doc-'+role,order=len(blocks),kind='paragraph',
                    content=dict(type='text',text=text),source_locator=f'PDF第{number}页',version=1,editable=role=='new'))
    source=dict(id='doc-'+role,role=role,filename=path.name,report_version=version,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),identity_kind='file_bytes',
        coverage=dict(status='partial',omissions=notes+['PDF文字块提取不恢复原表格结构、脚注语义或图像结构；完整页面在原稿视图保留。'],notes='直接PDF输入：正文来自文本层，可能含页眉页脚；无OCR，不把扫描图像当文字。'))
    return source,blocks


def compare_pdfs(old,new,old_version,new_version):
    od,a=extract_pdf(old,'old',old_version);nd,b=extract_pdf(new,'new',new_version)
    if not a or not b:raise ContractError('At least one PDF has no text layer; OCR is outside current scope')
    d=dict(schema_version='1.0',package_id='pdf-review-'+uuid4().hex,title=Path(new).stem+' · PDF修订对照',review_revision=0,parent_revision=None,
        generated_at=datetime.now(timezone.utc).isoformat(),source_documents=[od,nd],blocks=a+b,relations=match(a,b),reasons=[],changes=[],opinions=[],decisions=[],
        summary=dict(text='PDF文本层候选对照；原页完整保留，未配对及低置信度内容需核查。修改原因、专业影响与专家意见尚未登记。',change_ids=[],review_status='unreviewed'),
        working_copy=dict(overrides={},state_hash='0'*64,report_sync='source_baseline'),operations=[],relation_history=[])
    return attach(validate(seal(d)),old,new)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('attach');p.add_argument('input');p.add_argument('old_pdf');p.add_argument('new_pdf');p.add_argument('output')
    p=sub.add_parser('compare');p.add_argument('old_pdf');p.add_argument('new_pdf');p.add_argument('output');p.add_argument('--old-version',required=True);p.add_argument('--new-version',required=True)
    a=parser.parse_args()
    result=attach(load(a.input),a.old_pdf,a.new_pdf) if a.cmd=='attach' else compare_pdfs(a.old_pdf,a.new_pdf,a.old_version,a.new_version)
    save(a.output,result)
    print({k:dict(pages=len(v['pages']),mapped=len(v['locations']),unmapped=len(v['unmapped_ids'])) for k,v in result['page_previews'].items()})
