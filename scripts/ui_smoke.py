"""Browser UI checks against a running Workbench.

Default: native browser HTTP. --bridge: inject local assets into about:blank and
transport fetch calls through a Python HTTP client. This fallback does NOT test
native browser networking/CSP/clipboard/download prompts; the report says so.
"""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path
import re
import time

import httpx
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--url',default='http://127.0.0.1:8765')
    ap.add_argument('--bridge',action='store_true')
    ap.add_argument('--executable')
    ap.add_argument('--out',type=Path,default=ROOT/'qa')
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    client=httpx.Client(base_url=args.url,timeout=40)
    config=client.get('/api/config').json();client.headers['X-Workbench-Token']=config['token']
    existing={t['id'] for t in client.get('/api/twins').json()['twins']}
    checks=[];js_errors=[];requests=[]
    def ok(name):checks.append({'name':name,'status':'passed'})
    def bridge(req):
        response=client.request(req['method'],req['path'],headers=req['headers'],content=base64.b64decode(req['body']) if req.get('body') else None)
        requests.append({'method':req['method'],'path':req['path'],'status':response.status_code})
        return {'status':response.status_code,'headers':dict(response.headers),'body':base64.b64encode(response.content).decode()}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path=args.executable,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1512,'height':1050},device_scale_factor=1)
            page.on('pageerror',lambda e:js_errors.append(str(e)))
            if args.bridge:
                page.expose_function('__http_bridge',bridge)
                html=(ROOT/'workbench/static/index.html').read_text(encoding='utf-8')
                html=re.sub(r'<script.*?</script>','',html,flags=re.S)
                html=re.sub(r'<link[^>]*>','',html)
                page.set_content(html)
                page.add_style_tag(content=(ROOT/'workbench/static/app.css').read_text(encoding='utf-8'))
                page.evaluate('''base => {
                  const store={};Object.defineProperty(window,'localStorage',{value:{getItem:k=>store[k]??null,setItem:(k,v)=>{store[k]=String(v)},removeItem:k=>delete store[k]}});
                  window.fetch=async(url,opts={})=>{
                    const req=new Request(new URL(url,base),opts),data=new Uint8Array(await req.arrayBuffer());
                    let bin='';for(let i=0;i<data.length;i+=8192)bin+=String.fromCharCode(...data.slice(i,i+8192));
                    const result=await window.__http_bridge({path:new URL(req.url).pathname,method:req.method,headers:Object.fromEntries(req.headers.entries()),body:btoa(bin)});
                    return new Response(Uint8Array.from(atob(result.body),c=>c.charCodeAt(0)),{status:result.status,headers:result.headers});
                  };
                }''',args.url)
                charts=(ROOT/'workbench/static/charts.js').read_text(encoding='utf-8').replace('export ','')
                app=(ROOT/'workbench/static/app.js').read_text(encoding='utf-8').split('\n',1)[1]
                page.evaluate('(async()=>{'+charts+'\n'+app+'})()')
            else:
                page.goto(args.url)
            page.wait_for_selector('#build-button')
            page.screenshot(path=str(args.out/'ui-build-empty.png'),full_page=True)
            ok('six-page-navigation-mounted')
            raw=(ROOT/'data/demo/consumer_001.csv').read_bytes()
            page.locator('#csv-files').set_input_files({'name':'소비001_업로드검증.csv','mimeType':'text/csv','buffer':raw})
            page.wait_for_selector('.file-list')
            assert '소비001_업로드검증.csv' in page.locator('.file-list').inner_text()
            ok('korean-filename-csv-upload-profile')
            page.locator('[data-action="snapshot-type"][data-type="manual"]').click()
            page.locator('[data-manual="accounts.0.balance"]').fill('3450000')
            page.locator('[data-manual="cards.0.kind"]').select_option('CREDIT')
            page.locator('[data-manual="cards.0.opening"]').fill('0')
            page.locator('[data-manual="reserve"]').fill('150000')
            page.locator('[data-action="manual-to-json"]').click()
            manual=json.loads(page.locator('#snapshot-json').input_value())
            assert manual['accounts'][0]['balance_krw']==3450000
            assert manual['source']=='USER_ASSUMPTION'
            assert manual['cards'][0]['kind']=='CREDIT'
            ok('manual-snapshot-to-exact-json')
            page.locator('#twin-name').fill('UI QA snapshot Twin')
            page.locator('#build-button').click()
            page.wait_for_selector('.build-result',timeout=30000)
            built=json.loads(page.locator('.build-result pre').inner_text())
            assert built['state']['managed_cash_krw']==3450000
            snapshot_id=page.locator('#active-twin').input_value()
            ok('csv-and-manual-state-build-real-engine')
            page.screenshot(path=str(args.out/'ui-build-created.png'),full_page=True)

            def route(mode):
                page.locator(f'nav a[href="#/{mode}"]').click()
                page.wait_for_selector('#mode-form')
            def run(mode):
                page.locator('#run-button').click()
                page.wait_for_function("document.querySelector('#output-badge')?.textContent !== 'RUNNING'")
                page.wait_for_selector('.json-toolbar',timeout=30000)
                result=json.loads(page.locator('#output-content pre').inner_text())
                assert result['mode']==mode
                return result
            for mode in ['forecast','what_if','goal','risk','optimize']:
                route(mode)
                page.locator('[data-path="horizon_days"]').fill('30')
                page.locator('[data-path="paths"]').fill('20')
                page.locator('[data-path="seed"]').fill('123')
                if mode=='what_if':
                    page.locator('[data-path="scenario.expense_reductions.외식"]').fill('35')
                    page.locator('[data-path="scenario.expense_reductions.쇼핑"]').fill('12')
                    page.locator('[data-path="scenario.income_multiplier"]').fill('95')
                    page.get_by_text('일회성 현금 이벤트 · 0건',exact=True).click()
                    page.locator('[data-action="add-event"]').click()
                    page.locator('[data-path="scenario.cash_events.0.amount_krw"]').fill('150000')
                if mode in ('goal','optimize'):
                    page.locator('[data-path="goal.target_krw"]').fill('650000')
                    page.locator('[data-path="goal.reserve_krw"]').fill('100000')
                    page.locator('[data-path="goal.success_probability"]').fill('60')
                if mode=='risk':
                    page.locator('[data-path="stress_scenarios.0.income_multiplier"]').fill('50')
                if mode=='optimize':
                    page.locator('#reduction-grid').fill('0, 15, 30')
                    page.locator('[data-path="optimization.max_shortfall_probability"]').fill('50')
                result=run(mode)
                assert result['model']['seed']==123 and result['model']['paths']==20
                assert result['horizon_days']==30
                page.locator('[data-action="output-tab"][data-tab="request"]').click()
                request=json.loads(page.locator('#output-content pre').inner_text())
                if mode=='what_if':
                    assert request['scenario']['expense_reductions']['외식']==.35
                    assert request['scenario']['cash_events'][0]['amount_krw']==150000
                if mode=='optimize':
                    assert request['optimization']['reduction_grid']==[0,.15,.3]
                page.locator('[data-action="output-tab"][data-tab="metrics"]').click()
                assert page.locator('.metric-tile').count()>=2
                page.locator('[data-action="output-tab"][data-tab="charts"]').click()
                assert page.locator('.chart-card svg').count()>=1
                page.locator('[data-action="output-tab"][data-tab="warnings"]').click()
                assert 'UNCALIBRATED_MODEL' in page.locator('#output-content').inner_text()
                page.locator('[data-action="output-tab"][data-tab="json"]').click()
                page.screenshot(path=str(args.out/f'ui-{mode}.png'),full_page=True)
                ok(mode+'-editable-form-real-output-and-five-tabs')

            route('forecast')
            page.locator('[data-path="horizon_days"]').fill('60')
            assert page.locator('#stale-note').is_visible()
            ok('stale-result-explicit-after-input-change')
            page.locator('[data-action="editor"][data-editor="json"]').click()
            page.locator('#request-json').fill('{broken')
            page.locator('#run-button').click()
            page.wait_for_selector('.inline-error')
            ok('malformed-json-user-visible-error')
            page.locator('#request-json').fill(json.dumps({'mode':'forecast','paths':1}))
            page.locator('#run-button').click()
            page.wait_for_function("document.querySelector('.inline-error')?.textContent.includes('SCHEMA_VALIDATION')")
            ok('server-schema-error-visible')
            page.locator('#request-json').fill(json.dumps({'mode':'forecast','horizon_days':7,'paths':20,'seed':7}))
            page.locator('[data-action="output-tab"][data-tab="json"]').click()
            result=run('forecast')
            assert result['horizon_days']==7 and result['model']['seed']==7
            ok('raw-json-edit-runs-numeric-engine')
            request_file=json.dumps({'mode':'forecast','horizon_days':9,'paths':20,'seed':9}).encode()
            page.locator('#request-file').set_input_files({'name':'custom.json','mimeType':'application/json','buffer':request_file})
            page.wait_for_function("document.querySelector('#request-json')?.value.includes('9')")
            assert json.loads(page.locator('#request-json').input_value())['seed']==9
            ok('request-json-file-import')

            route('optimize')
            page.locator('#run-button').click()
            page.wait_for_selector('.busy-banner [data-action="cancel-job"]:not([disabled])')
            page.locator('.busy-banner [data-action="cancel-job"]').click()
            page.wait_for_selector('.run-status.cancelled',timeout=15000)
            ok('numeric-job-cancel-from-ui')

            page.locator('nav a[href="#/build"]').click()
            page.locator('[data-action="snapshot-type"][data-type="history"]').click()
            page.locator('#twin-name').fill('UI QA history only')
            page.locator('#build-button').click()
            page.wait_for_function("document.querySelector('.build-result pre')?.textContent.includes('\\\"absolute_cash_ready\\\": false')",timeout=30000)
            route('goal')
            page.locator('[data-action="output-tab"][data-tab="json"]').click()
            result=run('goal')
            assert result['status']=='insufficient_data'
            assert result['metrics']['terminal_cash_p50_krw']['value'] is None
            ok('history-only-insufficient-data-not-zero')

            page.locator('#active-twin').select_option(snapshot_id)
            page.wait_for_function("document.querySelector('.twin-meta-bar')?.textContent.includes('UI QA snapshot Twin')")
            page.set_viewport_size({'width':390,'height':844})
            for mode in ['build','forecast','what_if','goal','risk','optimize']:
                page.evaluate('(hash)=>location.hash=hash','#/'+mode)
                page.wait_for_function('(label)=>document.querySelector("#crumb").textContent===label',arg={'build':'Twin 생성','forecast':'미래 예측','what_if':'What-if 비교','goal':'목표 가능성','risk':'위험 분석','optimize':'행동 최적화'}[mode])
                assert page.evaluate('document.documentElement.scrollWidth <= 390'), mode+' horizontally overflows'
            page.screenshot(path=str(args.out/'ui-mobile.png'),full_page=True)
            ok('all-six-pages-mobile-no-horizontal-overflow')
            assert not js_errors,js_errors
            ok('zero-uncaught-javascript-errors')
            browser.close()
    finally:
        # Only remove Twins made by this QA run, never pre-existing user data.
        for t in client.get('/api/twins').json()['twins']:
            if t['id'] not in existing and t['name'].startswith('UI QA'):
                client.delete('/api/twins/'+t['id'])
        report={'transport':'dom-injection + real-http-bridge' if args.bridge else 'native-browser-http',
                'limitations':['Native browser HTTP, CSP enforcement, OS clipboard and actual file-download prompts are not verified by bridge mode.'] if args.bridge else [],
                'checks':checks,'passed':len(checks),'javascript_errors':js_errors,'http_calls':requests}
        (args.out/'ui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(checks),'javascript_errors':js_errors},ensure_ascii=False))

if __name__=='__main__':main()
