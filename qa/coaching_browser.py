"""Browser acceptance against the real loopback server, never mocked API results."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    with tempfile.TemporaryDirectory() as data, (ROOT/'qa/coaching-browser-server.log').open('w') as log:
        server = subprocess.Popen([sys.executable, str(ROOT/'launcher.py'), '--no-install', '--no-browser',
                                   '--port', str(port), '--data-dir', data], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=base, timeout=10) as client:
                deadline = time.monotonic()+30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError('Server exited: '+(ROOT/'qa/coaching-browser-server.log').read_text())
                    try:
                        response = client.get('/api/config')
                        response.raise_for_status()
                        break
                    except httpx.TransportError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(.1)
                client.headers['X-Workbench-Token'] = response.json()['token']
                csv = ROOT/'data/demo/consumer_003.csv'
                upload = client.post('/api/uploads', files={'files': (csv.name, csv.read_bytes(), 'text/csv')})
                upload.raise_for_status()
                snapshot = json.loads((ROOT/'examples/snapshot_003.json').read_text())
                response = client.post('/api/twins', json={'upload_id': upload.json()['upload_id'], 'name': '생활 코칭 검증', 'snapshot': snapshot})
                response.raise_for_status()
                job_id = response.json()['id']
                deadline = time.monotonic()+30
                while True:
                    job = client.get('/api/jobs/'+job_id).json()
                    if job['status'] == 'succeeded':
                        break
                    assert job['status'] == 'running', job
                    assert time.monotonic() < deadline, 'Build timed out'
                    time.sleep(.1)
            with sync_playwright() as browser_api:
                browser = browser_api.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                failures = []
                page.on('pageerror', lambda exc: failures.append(str(exc)))
                page.goto(base)
                expect(page.locator('#brief')).to_be_visible(timeout=30000)
                expect(page.locator('#review')).to_be_enabled()
                expect(page.locator('#error')).to_be_hidden()
                if page.locator('#replay').is_visible():
                    page.locator('#replay').click()
                expect(page.locator('#cash-section')).to_be_visible(timeout=30000)
                expect(page.locator('#compare')).to_be_enabled()
                page.locator('#change-kind').select_option('set_aside')
                page.locator('#amount').fill('100000')
                page.locator('#payer').select_option('account:'+snapshot['accounts'][0]['account_id'])
                page.locator('#compare').click()
                expect(page.locator('#comparison')).to_be_visible(timeout=30000)
                expect(page.locator('#compare')).to_be_enabled()
                expect(page.locator('#error')).to_be_hidden()
                evidence = json.loads(page.locator('#raw-result').text_content())
                effect = evidence['comparison']['effect']
                assert effect['terminal_cash_change']['p50_krw'] == 0
                assert effect['terminal_after_earmark_change']['p50_krw'] == -100000
                page.screenshot(path=str(ROOT/'qa/coaching-browser-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(ROOT/'qa/coaching-browser-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'Mobile page overflows horizontally'
                assert not failures, failures
                browser.close()
                print('PASS: actual-server desktop/mobile check-in, replay, earmark comparison, no browser exceptions or horizontal overflow')
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    main()
