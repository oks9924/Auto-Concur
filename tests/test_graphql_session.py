"""실제 Chromium으로 '세션을 공유한다'는 가정을 검증한다.

이 설계 전체가 한 가정에 걸려 있다 - `browser.launch` 가 준 컨텍스트의 `request` 가
**그 컨텍스트의 쿠키로** 나간다는 것. 그게 아니면 새 자격증명이 필요해지고, 그건
이 저장소가 하지 않기로 한 일이다. 그래서 가짜가 아니라 실제 브라우저로 잰다.

로컬 서버만 쓴다. Concur에 로그인하지 않는다.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright, Error

from src import concur_graphql as gq

SEEN = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        page = b'<!doctype html><title>t</title><body>ok</body>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        sent = json.loads(self.rfile.read(length)) if length else {}
        SEEN.append({'cookie': self.headers.get('Cookie'),
                     'contentType': self.headers.get('Content-Type'),
                     'body': sent})
        name = sent.get('operationName')
        if name == 'GetReportIdAndKey':
            data = {'employee': {'userId': 'U1', 'contextRole': 'TRAVELER',
                                 'expenseReport': {'reportId': 'R1', 'rptKey': 'K1'}}}
        else:
            data = {'reportEntriesDetails': {'reportId': 'R1', 'entries': [{
                'expenseId': 'E1',
                'summary': {'id': 'E1', 'parentExpenseId': None,
                            'transactionDate': '2026-09-07',
                            'transactionAmount': {'value': 450000, 'currencyCode': 'KRW'},
                            'vendor': {'description': '가맹점', 'name': None},
                            'expenseType': {'id': '1', 'code': 'LODNG', 'name': '숙박비'},
                            'receiptImageId': 'IMG1', 'eReceiptImageId': None,
                            'isImageRequired': True,
                            'meta': {'hasReceiptImage': True, 'hasSourceCreditCard': True,
                                     'hasSourceEReceipt': False}}}]}}
        raw = json.dumps({'data': data}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope='module')
def server():
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()


@pytest.fixture
def ctx(server, monkeypatch, tmp_path):
    SEEN.clear()
    monkeypatch.setattr(gq, 'ENDPOINT', server + '/spend-graphql/graphql')
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                headless=True, args=['--no-sandbox'],
                executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        except Error as exc:
            pytest.skip(f'Chromium 테스트 실행 파일 없음: {exc}')
        context = browser.new_context()
        # 사람이 로그인해서 생긴 쿠키를 흉내낸다.
        context.add_cookies([{'name': 'session', 'value': 'from-browser',
                              'url': server}])
        context.new_page().goto(server + '/')
        yield context
        context.close()
        browser.close()


def test_브라우저_쿠키로_나간다(ctx):
    """이 설계의 전제. 새 자격증명 없이 사람의 세션을 그대로 쓴다."""
    gq.identify(ctx, 'R1')
    assert SEEN, '서버가 호출을 못 받았습니다'
    assert 'session=from-browser' in (SEEN[0]['cookie'] or ''), \
        'ctx.request 가 브라우저 컨텍스트의 쿠키를 보내지 않았습니다'


def test_JSON으로_보낸다(ctx):
    gq.identify(ctx, 'R1')
    assert 'application/json' in (SEEN[0]['contentType'] or '')
    body = SEEN[0]['body']
    assert body['operationName'] == 'GetReportIdAndKey'
    assert body['variables']['reportId'] == 'R1'
    assert 'query GetReportIdAndKey' in body['query']


def test_목록을_끝까지_읽는다(ctx):
    user_id, role = gq.identify(ctx, 'R1')
    rows = gq.top_level(gq.entries(ctx, 'R1', user_id, role))
    assert len(rows) == 1
    assert str(rows[0].when) == '2026-09-07' and rows[0].amount == 450000
    assert rows[0].has_receipt is True and rows[0].note == ''


def test_mutation_은_부르지_않는다(ctx):
    """읽기 단계다. 보낸 질의문에 mutation 이 하나도 없어야 한다."""
    user_id, role = gq.identify(ctx, 'R1')
    gq.entries(ctx, 'R1', user_id, role)
    for call in SEEN:
        assert 'mutation' not in call['body'].get('query', ''), call['body'].get('operationName')
