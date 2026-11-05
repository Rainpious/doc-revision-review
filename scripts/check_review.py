"""Browser checks for a newly built comparison; retain screenshots for visual review."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))
from revision_tool import load,save,validate
from audit_review import audit,read_input,save_audit,check_assets


def main():
    from playwright.sync_api import sync_playwright
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('html',type=Path);p.add_argument('--evidence',required=True,type=Path)
    p.add_argument('--old',type=Path);p.add_argument('--new',type=Path)
    a=p.parse_args();a.evidence.mkdir(parents=True,exist_ok=False);errors=[];network=[]
    package,html=read_input(a.html)
    if html is None:raise ValueError('Browser check requires HTML')
    report=audit(package,{role:path for role,path in [('old',a.old),('new',a.new)] if path},html)
    save_audit(report,a.evidence/'content-audit.json')
    if not report['runtime']['embedded_assets_match']:
        save(a.evidence/'browser-result.json',dict(status='failed',runtime=report['runtime'],content_verified=False,failure='Embedded viewer differs from pinned release; preserve historical HTML, rebuild a new file from its package.'))
        raise ValueError('Embedded viewer differs from pinned release; see content-audit.json')
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport=dict(width=1600,height=1100))
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:network.append(r.url) if r.url.startswith(('http:','https:')) else None)
        def ready(path):page.goto(path.resolve().as_uri());page.wait_for_function('window.RevisionApp&&!document.getElementById("app").hidden')
        ready(a.html);data=page.evaluate('RevisionApp.getCurrentPackage()');validate(data)
        previews=data.get('page_previews',{});pages=sum(len(x['pages']) for x in previews.values())
        assert page.locator('.page-image').count()==pages
        changed=[r for r in data['relations'] if r['content_changed'] or r['moved'] or r['kind']!='correspondence']
        setting=page.locator('#show-detail-revisions')
        assert not setting.is_checked(),'Details should be hidden by default'
        rect=setting.bounding_box();label=page.locator('.detail-setting').bounding_box();select=page.locator('#filter').bounding_box()
        assert select['y']+select['height']<=label['y']+2
        assert rect['width']<=20 and rect['x']<=label['x']+5 and label['height']<=36
        default_ids=page.locator('.nav-entry').evaluate_all('(els)=>els.map(e=>e.dataset.id)')
        assert set(default_ids)=={r['id'] for r in changed if not is_detail(data,r)}
        page.screenshot(path=str(a.evidence/'default.png'),full_page=True)
        setting.check()
        assert page.locator('.nav-entry').count()==len(changed)
        missing=[]
        for r in changed:
            page.locator(f'.nav-entry[data-id="{r["id"]}"]').click()
            for bid in r['from_ids']+r['to_ids']:
                target=page.locator('#block-'+bid)
                if not target.count():missing.append(bid);continue
                rect=target.bounding_box();pane=target.locator('xpath=ancestor::div[contains(@class,"pane-scroll")]').bounding_box()
                assert pane['y']-2<=rect['y']<pane['y']+pane['height'],bid
        page.screenshot(path=str(a.evidence/'pages.png'),full_page=True)
        if pages:
            toggle=page.locator('[data-action=view-text]');toggle.click();assert toggle.get_attribute('aria-pressed')=='true'
            assert page.locator('#app [data-block-id]').count()==len(data['blocks'])
            page.screenshot(path=str(a.evidence/'text.png'),full_page=True)
            toggle.click();assert toggle.get_attribute('aria-pressed')=='false'
        assert page.evaluate('RevisionApp.getCurrentPackage().working_copy.state_hash')==data['working_copy']['state_hash']
        exported=a.evidence/'exported.html';serialized=page.evaluate('RevisionApp.serializeOfflineHtml()')
        assert check_assets(serialized)['embedded_assets_match']
        exported.write_text(serialized,encoding='utf-8');ready(exported)
        assert not page.locator('#show-detail-revisions').is_checked()
        assert page.evaluate('RevisionApp.getCurrentPackage().working_copy.state_hash')==data['working_copy']['state_hash']
        page.set_viewport_size(dict(width=3440,height=1440));assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.set_viewport_size(dict(width=700,height=1000));assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors and not network,(errors,network);browser.close()
    save(a.evidence/'browser-result.json',dict(runtime=report['runtime'],content_verified=False,default_details_hidden=True,detail_setting_compact_below_filter=True,default_visible_changes=len(default_ids),navigation_scope='all',pages=pages,blocks=len(data['blocks']),changed_relations=len(changed),unmapped_changed_blocks=missing,source_pages_default=True,single_view_toggle=True,state_unchanged=True,export_reopen=True,wide_and_mobile_no_overflow=True,page_errors=errors,external_requests=network,visual_review='Screenshots generated; agent must inspect them'))
    print('browser checks passed; inspect evidence screenshots')


def is_detail(data,r):
    import re
    bm={b['id']:b for b in data['blocks']}
    pattern=r'^(编制日期|修订日期|发布日期|报告日期|资料截止日期|版本|版次|报告名称|报告类型|评价类型|编制单位|编制人|审核人|审定人|总页数)(?:[：:\s]|$)'
    for bid in r['from_ids']+r['to_ids']:
        b=bm[bid];text=data['working_copy']['overrides'].get(bid,{}).get('text',b['content'].get('text',''))
        if b['content']['type']!='text' or b['order']>=30 or len(text.encode('utf-16-le'))//2>=240 or not re.search(pattern,text.strip()):return False
    return True


if __name__=='__main__':main()
