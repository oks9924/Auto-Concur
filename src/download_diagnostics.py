"""Local download lifecycle diagnostics; never record card data or session URLs."""
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import tempfile
import time
from uuid import uuid4


def save_bundle(download, raw_dir: Path) -> Path:
    """Complete transfer before publishing; a failed transfer keeps older files."""
    name = PureWindowsPath(str(download.suggested_filename)).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:160]
    if not name or name.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(10)], *[f'LPT{i}' for i in range(10)]}:
        name = 'receipt-bundle.bin'
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / name
    if target.exists():
        target = raw_dir / f'{target.stem}-{uuid4().hex[:12]}{target.suffix}'
    with tempfile.TemporaryDirectory(prefix='.receipt-transfer-', dir=raw_dir) as staging:
        temporary = Path(staging) / 'bundle.part'
        download.save_as(temporary)
        if not temporary.is_file() or not temporary.stat().st_size:
            raise OSError('다운로드가 끝났지만 저장된 파일이 비어 있습니다.')
        os.replace(temporary, target)
    return target


def error_kind(exc):
    text = str(exc).lower()
    if 'has been closed' in text or 'targetclosed' in type(exc).__name__.lower():
        return 'target_closed'
    if 'timeout' in text or 'timeout' in type(exc).__name__.lower():
        return 'timeout'
    if 'cancel' in text:
        return 'cancelled'
    if isinstance(exc, OSError):
        return 'local_file_error'
    return 'other'


class DownloadWatch:
    """Capture order before sync_playwright's exception cleanup closes windows.

    Lifecycle events identify what was observed, not who closed the browser.
    No retries, security bypasses, network recording, or Concur calls.
    """
    def __init__(self, context, out_dir):
        self.context = context
        self.out_dir = Path(out_dir)
        self.started = time.monotonic()
        self.phase = 'opened'
        self.events = []
        self.listeners = []
        self.pages = []
        self.diagnostic_path = None

    def mark(self, phase):
        self.phase = phase
        self.record(phase)

    def record(self, event, page=None):
        item = {'elapsed_ms': round((time.monotonic() - self.started) * 1000),
                'event': event, 'phase': self.phase}
        if page is not None:
            item['page'] = page
        self.events.append(item)
        del self.events[:-200]

    def listen(self, obj, name, callback):
        obj.on(name, callback)
        self.listeners.append((obj, name, callback))

    def watch_page(self, page):
        if page in self.pages:
            return
        self.pages.append(page)
        number = len(self.pages)
        self.record('page_observed', number)
        for name in ('close', 'crash', 'download'):
            self.listen(page, name, lambda *_, n=name, i=number: self.record('page_' + n, i))

    def __enter__(self):
        self.listen(self.context, 'page', self.watch_page)
        self.listen(self.context, 'close', lambda *_: self.record('context_close'))
        if self.context.browser is not None:
            self.listen(self.context.browser, 'disconnected', lambda *_: self.record('browser_disconnected'))
        for page in self.context.pages:
            self.watch_page(page)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc is not None:
                self.record('exception_before_playwright_cleanup')
                diagnostic = {
                    'version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
                    'stage': self.phase, 'error_type': type(exc).__name__, 'error_kind': error_kind(exc),
                    'python': platform.python_version(), 'platform': platform.system(),
                    'playwright': version('playwright'),
                    'page_states': [{'page': i, 'closed': p.is_closed()} for i, p in enumerate(self.pages, 1)],
                    'events': self.events,
                    'note': 'Observed before cleanup. Events do not prove crash cause or who closed a window. No URLs, card values, cookies or filenames collected.'}
                if self.context.browser is not None:
                    diagnostic['browser_connected'] = self.context.browser.is_connected()
                folder = self.out_dir / 'inspect-out'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('download-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
                path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
                self.diagnostic_path = path
                print(f'다운로드 실패 단계: {self.phase} · 종류: {diagnostic["error_kind"]}')
                print(f'  종료 순서 진단: {path}')
                print('  자동 재다운로드하지 않았습니다. 기존 전표와 작업지는 유지됩니다.')
        except Exception as diagnostic_error:
            # A diagnostic failure must never replace the original download error.
            print(f'  다운로드 진단 기록 실패: {type(diagnostic_error).__name__}')
        finally:
            for obj, name, callback in reversed(self.listeners):
                try:
                    obj.remove_listener(name, callback)
                except Exception:
                    pass  # The transport may have already disconnected.
        return False
