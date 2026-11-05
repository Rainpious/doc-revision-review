"""Read-only PDF glyph mapping for inline source-page revision decorations."""
import copy
import hashlib
from pathlib import Path
import pymupdf
from pdf_input import norm, coords
from revision_tool import validate, seal, ContractError


def glyph_map(path, blocks, locations):
    stream=[]; refs=[]
    with pymupdf.open(path) as pdf:
        for number,page in enumerate(pdf,1):
            for block in page.get_text('rawdict',sort=True)['blocks']:
                if block['type']!=0:continue
                for line in block['lines']:
                    for span in line['spans']:
                        for char in span['chars']:
                            value=norm(char['c'])
                            if not value:continue
                            box=coords(number,char['bbox'],page)
                            stream.append(value);refs.extend([box]*len(value))
    text=''.join(stream);result={}
    for block in blocks:
        bid=block['id'];loc=locations.get(bid)
        if block['content']['type']!='text' or not loc or loc['method']!='exact_text':continue
        source=block['content']['text'];target=norm(source)
        if not target:continue
        hits=[];start=text.find(target)
        while start>=0:
            sample=refs[start:start+len(target)]
            # Require every glyph inside an already verified line region.
            if all(any(box['page']==b['page'] and box['x']>=b['x']-10 and box['y']>=b['y']-10 and box['x']+box['w']<=b['x']+b['w']+10 and box['y']+box['h']<=b['y']+b['h']+10 for b in loc['boxes']) for box in sample):hits.append(start)
            start=text.find(target,start+1)
        if len(hits)!=1:continue
        cursor=hits[0];offset=0;glyphs=[]
        for char in source:
            width=len(char.encode('utf-16-le'))//2
            value=norm(char)
            for i in range(len(value)):
                box=refs[cursor+i]
                item=dict(start=offset,end=offset+width,box=box)
                if not glyphs or glyphs[-1]!=item:glyphs.append(item)
            cursor+=len(value);offset+=width
        result[bid]=glyphs
    return result


def attach_glyphs(data,old_pdf,new_pdf):
    validate(data);out=copy.deepcopy(data)
    for role,path in [('old',old_pdf),('new',new_pdf)]:
        doc=next(d for d in out['source_documents'] if d['role']==role)
        preview=out['page_previews'][doc['id']]
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=preview['pdf_sha256']:raise ContractError('Glyph PDF identity differs from page preview')
        preview['glyphs']=glyph_map(path,[b for b in out['blocks'] if b['document_id']==doc['id']],preview['locations'])
    return validate(seal(out))
