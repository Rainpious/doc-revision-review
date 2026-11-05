"""Package the local application and static fallback without runtime dependencies."""
import base64
import hashlib
import html
import re
from revision_tool import ROOT, render, script_json, validate


def build(data):
    validate(data)
    old = render(data)
    body = old.split('<body>', 1)[1].split('<script id="revision-package"', 1)[0]
    # Baseline layout is only a no-JS fallback; remove baseline class collisions.
    body = body.replace('class="pane"', 'class="static-document"')
    body = body.replace('静态参考页，交互尚未接入', '静态阅读副本（启用 JavaScript 可使用交互）')
    if data.get('page_previews'):
        # Page images already show every original figure. Avoid duplicating full-size
        # figures in fallback; extracted text/tables remain available under details.
        body = re.sub(r'<img\b[^>]*>', '', body)
        page_body = '<h1>' + html.escape(data['title']) + '</h1><p>PDF原页阅读副本；启用JavaScript可关联定位及切换文字审阅。</p><div class="static-panes">'
        for role in ('old', 'new'):
            doc = next(d for d in data['source_documents'] if d['role']==role)
            page_body += '<section><h2>' + ('旧稿' if role=='old' else '新稿') + ' · ' + html.escape(doc['report_version']) + '</h2>'
            for page in data['page_previews'][doc['id']]['pages']:
                page_body += f'<figure class="static-page"><img src="{page["data_url"]}" alt="PDF第{page["number"]}页"><figcaption>第{page["number"]}页</figcaption></figure>'
            page_body += '</section>'
        body = page_body + '</div><details class="static-extracted"><summary>查看提取文字、说明与来源</summary>' + body + '</details>'
    js = '\n'.join((ROOT / 'viewer' / f).read_text(encoding='utf-8') for f in ('engine.js', 'app.js'))
    if '</script' in js.lower():
        raise ValueError('unsafe closing script sequence in app asset')
    digest = base64.b64encode(hashlib.sha256(js.encode('utf-8')).digest()).decode()
    csp = f"default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; base-uri 'none'; form-action 'none'"
    css = (ROOT / 'viewer' / 'app.css').read_text(encoding='utf-8')
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + \
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}"><title>{html.escape(data["title"])}</title><style id="app-style">{css}</style></head><body>' + \
        f'<div id="static-view">{body}</div><div id="app" hidden></div><dialog id="modal"></dialog>' + \
        f'<script id="revision-package" type="application/json">{script_json(data)}</script><script id="app-code">{js}</script></body></html>\n'
