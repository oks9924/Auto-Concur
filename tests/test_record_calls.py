"""실제 Chromium과 로컬 서버로 호출 기록기를 검증한다.

핵심 검증 두 가지다.
  1. 날짜·금액이 '어떤 종류'로 기록되는가 (조사의 목적)
  2. 실제 값이 기록에 남지 않는가 (카드 데이터를 적지 않는다는 약속)
그리고 감싼 fetch가 원래 동작을 깨지 않는지도 본다 - 깨면 사용자의 실제
작업이 망가진다.

AUTO_CONCUR_TEST_BROWSER로 설치된 Chromium 실행 파일을 지정할 수 있다.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright, Error

from src.record_calls import RECORD_JS, DRAIN_JS, summary

# 기록에 절대 남아서는 안 되는 값들. 실제 전표에서 나올 만한 모양으로 둔다.
SECRETS = ['2026-09-07', '09/07/2026', '450000', '라한호텔울산', '12345678',
           'kyungsik.oh', '17,000']

PAYLOAD = {
    'entries': [
        {'entryId': 'E1A2B3C4D5E6F708',
         'transactionDate': '2026-09-07',
         'transactionAmount': 450000,
         'vendorDescription': '라한호텔울산',
         'approvalCode': '12345678',
         'attendees': [{'name': 'kyungsik.oh', 'count': 2}],
         'hasReceipt': True}
    ],
    'shownDate': '09/07/2026',
    'shownAmount': '17,000',
    'currency': 'KRW',
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/':
            page = b'<!doctype html><title>t</title><body>ready</body>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        else:
            self._json(PAYLOAD)

    def _read(self):
        length = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(length) if length else b''

    def do_PUT(self):
        self._read()
        self._json({'saved': True, 'entryId': 'E1A2B3C4D5E6F708'})

    def do_POST(self):
        self._read()
        self._json({'saved': True})


@pytest.fixture(scope='module')
def server():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()


@pytest.fixture(scope='module')
def page_factory():
    with sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(
                headless=True, args=['--no-sandbox'],
                executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        except Error as exc:
            pytest.skip(f'Chromium 테스트 실행 파일 없음: {exc}')
        yield instance
        instance.close()


@pytest.fixture
def recorded(page_factory, server):
    """페이지를 열고, 호출을 시키고, 걷어온 기록을 준다."""
    ctx = page_factory.new_context()
    ctx.add_init_script(RECORD_JS)
    page = ctx.new_page()
    page.goto(server + '/')

    def run(script):
        page.evaluate(script)
        page.wait_for_timeout(300)
        return page.evaluate(DRAIN_JS)

    yield run
    ctx.close()


def test_날짜가_ISO면_ISO라고_적는다(recorded, server):
    calls = recorded(f"""async () => {{
      await fetch('{server}/nui/expense/reports/A1B2C3D4E5F6/expenses');
    }}""")
    entry = next(c for c in calls if c['method'] == 'GET')
    first = entry['resBody']['entries']['[0]']
    assert first['transactionDate'] == 'str:date-iso'
    assert first['transactionAmount'] == 'int'
    assert entry['resBody']['shownDate'] == 'str:date-slash'
    assert entry['resBody']['shownAmount'] == 'str:number-grouped'


def test_실제_값은_어디에도_남지_않는다(recorded, server):
    calls = recorded(f"""async () => {{
      await fetch('{server}/nui/expense/reports/A1B2C3D4E5F6/expenses?sort=date');
      await fetch('{server}/nui/expense/entry/E1A2B3C4D5E6F708', {{
        method: 'PUT', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{transactionDate: '2026-09-07', vendorDescription: '라한호텔울산'}})
      }});
    }}""")
    text = json.dumps(calls, ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in text, f'{secret} 가 기록에 남았습니다'


def test_경로의_ID는_가린다(recorded, server):
    calls = recorded(f"""async () => {{
      await fetch('{server}/nui/expense/reports/A1B2C3D4E5F6/expenses?sort=date&page=2');
    }}""")
    entry = next(c for c in calls if c['method'] == 'GET')
    assert entry['path'] == '/nui/expense/reports/{id}/expenses'
    assert entry['query'] == ['page', 'sort']


def test_쓰기는_메서드와_본문_모양을_남긴다(recorded, server):
    calls = recorded(f"""async () => {{
      await fetch('{server}/nui/expense/entry/E1A2B3C4D5E6F708', {{
        method: 'PUT', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': 'x'}},
        body: JSON.stringify({{transactionDate: '2026-09-07', transactionAmount: 450000,
                              attendees: [{{name: 'kyungsik.oh'}}]}})
      }});
    }}""")
    put = next(c for c in calls if c['method'] == 'PUT')
    assert put['path'] == '/nui/expense/entry/{id}'
    assert put['reqBody']['transactionDate'] == 'str:date-iso'
    assert put['reqBody']['transactionAmount'] == 'int'
    assert put['reqBody']['attendees']['[0]']['name'] == 'str:len9-32'
    assert 'x-csrf-token' in put['reqHeaders']   # 토큰 이름만, 값은 없다
    assert put['status'] == 200


def test_XHR도_같이_기록한다(recorded, server):
    calls = recorded(f"""() => {{
      const x = new XMLHttpRequest();
      x.open('GET', '{server}/nui/expense/entry/E1A2B3C4D5E6F708');
      x.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      x.send();
    }}""")
    xhr = next(c for c in calls if c['via'] == 'xhr')
    assert xhr['method'] == 'GET' and xhr['status'] == 200
    assert xhr['resBody']['entries']['[0]']['transactionDate'] == 'str:date-iso'
    assert 'x-requested-with' in xhr['reqHeaders']


def test_영수증_업로드는_필드_이름만_남긴다(recorded, server):
    calls = recorded(f"""async () => {{
      const form = new FormData();
      form.append('file', new Blob(['%PDF-1.4 라한호텔울산 450000']), '2026-09-07-450000.pdf');
      form.append('entryId', 'E1A2B3C4D5E6F708');
      await fetch('{server}/nui/receipts/upload', {{method: 'POST', body: form}});
    }}""")
    post = next(c for c in calls if c['method'] == 'POST')
    assert post['reqBody'] == {'form-data-keys': ['entryId', 'file']}
    text = json.dumps(calls, ensure_ascii=False)
    assert '라한호텔울산' not in text and '2026-09-07-450000.pdf' not in text


def test_폼_인코딩은_키만_남긴다(recorded, server):
    calls = recorded(f"""async () => {{
      await fetch('{server}/nui/expense/save', {{
        method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: 'transactionDate=2026-09-07&amount=450000'
      }});
    }}""")
    post = next(c for c in calls if c['method'] == 'POST')
    assert post['reqBody'] == {'form-keys': ['amount', 'transactionDate']}


def test_앱이_읽은_값은_그대로다(page_factory, server):
    """clone을 안 쓰면 앱이 본문을 못 읽는다. 그러면 사용자의 실제 작업이 망가진다."""
    ctx = page_factory.new_context()
    ctx.add_init_script(RECORD_JS)
    page = ctx.new_page()
    page.goto(server + '/')
    seen = page.evaluate(f"""async () => {{
      const res = await fetch('{server}/nui/expense/entry/E1');
      const body = await res.json();
      return body.entries[0].transactionAmount;
    }}""")
    assert seen == 450000
    ctx.close()


def test_요약은_쓰기를_먼저_보여준다():
    records = [
        {'marker': '단계', 'step': 1},
        {'method': 'GET', 'host': 'h', 'path': '/a', 'status': 200},
        {'method': 'GET', 'host': 'h', 'path': '/a', 'status': 200},
        {'method': 'PUT', 'host': 'h', 'path': '/b', 'status': 200},
    ]
    lines = summary(records)
    assert 'PUT' in lines[0] and '/b' in lines[0]
    assert 'GET' in lines[1] and '2회' in lines[1]
