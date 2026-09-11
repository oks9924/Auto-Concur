"""리포트 준비와 대상 고정. 사용자 확인은 화면 준비 검증을 대신하지 않는다."""
from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error
from . import attach_receipts as ar


def report_key(url):
    parts = urlsplit(url)
    if not (parts.scheme == 'https' and (parts.hostname or '').endswith('.concursolutions.com')):
        return None
    match = re.fullmatch(r'/nui/expense/reports/([^/]+)(?:/expenses)?/?', parts.path)
    return (parts.netloc, match[1]) if match else None


@dataclass(frozen=True)
class Report:
    url: str
    key: tuple
    title: str
    rows: tuple


def signature(rows):
    return tuple((r.expense_id, r.when, r.amount, r.expense_type, r.has_receipt, r.receipt_file) for r in rows)


def ready_report(page, timeout=600):
    print('Concur에 로그인한 뒤 처리할 경비 리포트의 목록을 열어 주세요. 화면을 자동 확인합니다.')
    deadline, previous, repeats = time.monotonic() + timeout, None, 0
    while time.monotonic() < deadline:
        if page.is_closed():
            raise ar.AttachError('브라우저를 닫아 작업을 중단했습니다.')
        try:
            key = report_key(page.url)
            rows = ar.read_rows(page) if key else []
            complete = bool(rows) and all(r.expense_id and r.when and r.amount is not None for r in rows)
            current = (key, signature(rows)) if complete else None
            repeats = repeats + 1 if current and current == previous else 0
            previous = current
            if repeats >= 3:
                title = page.evaluate("() => document.querySelector('h1')?.innerText?.trim() || document.title")
                canonical = f'https://{key[0]}/nui/expense/reports/{key[1]}'
                return Report(canonical, key, title, tuple(rows))
        except Error:
            previous, repeats = None, 0
        page.wait_for_timeout(500)
    raise ar.AttachError('경비 목록 준비를 확인하지 못했습니다. 로그인·리포트 선택·경비 존재 여부를 확인해 주세요.')


def revalidate(page, report):
    if report_key(page.url) != report.key:
        raise ar.AttachError('선택한 리포트가 바뀌었습니다. C단계를 다시 시작해 작업 대상을 확인해 주세요.')
    rows = ar.rows_when_ready(page)
    if signature(rows) != signature(report.rows):
        raise ar.AttachError('시작 확인 중 경비 목록이 바뀌었습니다. 새 계획을 확인하도록 C단계를 다시 시작해 주세요.')


def check_context(page, report_url):
    wanted = report_key(report_url)
    parts = urlsplit(page.url)
    path = parts.path.split('/expenses/')[0].rstrip('/')
    current = report_key(f'{parts.scheme}://{parts.netloc}{path}')
    if not wanted or current != wanted:
        raise ar.AttachError('로그인 또는 리포트가 변경되어 작업을 중단했습니다.')
