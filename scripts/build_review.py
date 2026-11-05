"""Build a new offline report comparison with a pinned local runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

RUNTIME=Path(__file__).resolve().parent/'runtime'
sys.path.insert(0,str(RUNTIME))
from revision_tool import load,save,validate
from docx_input import compare
from pdf_input import attach,compare_pdfs
from interactive import build
from audit_review import audit, save_audit


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old',type=Path);p.add_argument('--new',type=Path)
    p.add_argument('--old-version');p.add_argument('--new-version');p.add_argument('--title')
    p.add_argument('--work',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--old-pdf',type=Path);p.add_argument('--new-pdf',type=Path)
    p.add_argument('--package',type=Path,help='Finalize a previously generated, validated package with annotations')
    a=p.parse_args();a.work=a.work.resolve();a.out=a.out.resolve()
    if a.out.exists():p.error('Output already exists; choose another filename')
    if not a.package and not all((a.old,a.new,a.old_version,a.new_version)):p.error('Need both source files and explicit version identifiers')
    if a.package and bool(a.old)!=bool(a.new):p.error('Supply both source files for finalization audit, or neither')
    if bool(a.old_pdf)!=bool(a.new_pdf):p.error('Provide both PDF previews or neither')
    release=load(RUNTIME/'release.json')
    for f in release['files']:
        if sha(RUNTIME/f['path'])!=f['sha256']:p.error('Pinned runtime differs from release manifest: '+f['path'])
    # Fresh workspaces avoid clobbering another review or unfinished run.
    a.work.mkdir(parents=True,exist_ok=False)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    sources={};stats={}
    if a.package:
        sources[str(a.package.resolve())]=sha(a.package);data=validate(load(a.package))
        for source in (a.old,a.new):
            if source:sources[str(source.resolve())]=sha(source)
    else:
        a.old=a.old.resolve();a.new=a.new.resolve()
        if a.out in (a.old,a.new):p.error('Output cannot replace a source')
        sources={str(f):sha(f) for f in (a.old,a.new)}
        if a.old.suffix.lower()==a.new.suffix.lower()=='.docx':
            data,stats=compare(a.old,a.new,a.old_version,a.new_version,a.title)
            save(a.work/'base.json',data)
            pdfs=[a.old_pdf,a.new_pdf]
            if not a.old_pdf:
                if os.name!='nt':raise RuntimeError('Word conversion requires Windows Word; supply both verified PDFs')
                pdfs=[a.work/'old.pdf',a.work/'new.pdf']
                for doc,pdf in zip((a.old,a.new),pdfs):
                    subprocess.run(['powershell.exe','-NoProfile','-File',str(RUNTIME/'word_to_pdf.ps1'),'-InputDocx',str(doc),'-OutputPdf',str(pdf)],check=True,timeout=180)
            else:
                for pdf in pdfs:sources[str(pdf.resolve())]=sha(pdf)
            data=attach(data,*pdfs)
        elif a.old.suffix.lower()==a.new.suffix.lower()=='.pdf':
            data=compare_pdfs(a.old,a.new,a.old_version,a.new_version)
        else:raise ValueError('Use two DOCX or two text-layer PDFs; mixed input is unsupported')
    validate(data);save(a.work/'package.json',data)
    for path,digest in sources.items():
        if sha(path)!=digest:raise RuntimeError('Input changed during build: '+path)
    # Exclusive creation also prevents a racing process from overwriting the output.
    html=build(data)
    content_audit=audit(data,{role:path for role,path in [('old',a.old),('new',a.new)] if path},html)
    audit_path=a.work/'content-audit.json';save_audit(content_audit,audit_path)
    with a.out.open('x',encoding='utf-8') as stream:stream.write(html)
    preview={docid:dict(pages=len(v['pages']),mapped=len(v['locations']),unmapped=len(v['unmapped_ids']),glyph_blocks=len(v.get('glyphs',{}))) for docid,v in data.get('page_previews',{}).items()}
    manifest=dict(runtime_release=release['release'],sources=sources,input_unchanged=True,html=str(a.out),bytes=a.out.stat().st_size,sha256=sha(a.out),package=str(a.work/'package.json'),state_hash=data['working_copy']['state_hash'],blocks=len(data['blocks']),relations=len(data['relations']),changed=sum(bool(r['content_changed'] or r['moved'] or r['kind']!='correspondence') for r in data['relations']),preview=preview,extraction=stats,browser_verified=False,content_verified=False,content_audit=str(audit_path),review_route=content_audit['triage']['route'])
    save(a.work/'build-result.json',manifest);print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
