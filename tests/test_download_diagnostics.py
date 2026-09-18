import json
from pathlib import Path
import pytest
from src.download_diagnostics import DownloadWatch, save_bundle, error_kind


class Emitter:
    def __init__(self):
        self.listeners = {}
    def on(self, event, callback):
        self.listeners.setdefault(event, []).append(callback)
    def remove_listener(self, event, callback):
        self.listeners[event].remove(callback)
    def emit(self, event):
        for callback in self.listeners.get(event, [])[:]:
            callback(self)
    def is_closed(self):
        return False
    def is_connected(self):
        return True


def context():
    ctx = Emitter(); ctx.pages = [Emitter()]; ctx.browser = Emitter()
    return ctx


def test_failure_captured_before_cleanup_without_sensitive_data(tmp_path):
    ctx = context()
    with pytest.raises(RuntimeError, match='has been closed'):
        with DownloadWatch(ctx, tmp_path) as watch:
            watch.mark('save_started')
            ctx.pages[0].emit('download')
            ctx.pages[0].emit('close')
            ctx.emit('close')
            raise RuntimeError('Target has been closed https://private.invalid/?card=123456')
    data = json.loads(watch.diagnostic_path.read_text(encoding='utf-8'))
    assert [v['event'] for v in data['events']][-4:] == ['page_download','page_close','context_close','exception_before_playwright_cleanup']
    assert data['stage'] == 'save_started' and data['error_kind'] == 'target_closed'
    assert '123456' not in str(data) and 'private.invalid' not in str(data)
    assert all(not callbacks for emitter in (ctx, ctx.browser, *ctx.pages) for callbacks in emitter.listeners.values())


def test_success_does_not_create_error_diagnostic(tmp_path):
    ctx = context()
    with DownloadWatch(ctx, tmp_path) as watch:
        watch.mark('save_completed'); watch.mark('normal_context_close'); ctx.emit('close')
    assert not (tmp_path/'inspect-out').exists()
    assert watch.events[-1]['phase'] == 'normal_context_close'


def test_diagnostic_io_failure_does_not_hide_original(tmp_path, monkeypatch):
    def fail(*a,**k): raise PermissionError('denied')
    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(RuntimeError, match='original'):
        with DownloadWatch(context(), tmp_path):
            raise RuntimeError('original')


class Download:
    suggested_filename = '전표.pdf'
    def __init__(self, payload=b'%PDF-test', failure=False):
        self.payload, self.failure = payload, failure
    def save_as(self, path):
        Path(path).write_bytes(self.payload)
        if self.failure:
            raise RuntimeError('Target has been closed')


def test_save_publishes_only_completed_file_and_keeps_previous(tmp_path):
    old = tmp_path/'전표.pdf'; old.write_bytes(b'previous')
    new = save_bundle(Download(), tmp_path)
    assert new != old and new.read_bytes() == b'%PDF-test'
    assert old.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.receipt-transfer-*'))


def test_failed_transfer_keeps_previous_and_cleans_only_own_staging(tmp_path):
    old = tmp_path/'전표.pdf'; old.write_bytes(b'previous')
    with pytest.raises(RuntimeError): save_bundle(Download(failure=True), tmp_path)
    assert list(tmp_path.iterdir()) == [old] and old.read_bytes() == b'previous'


def test_empty_transfer_not_published(tmp_path):
    with pytest.raises(OSError): save_bundle(Download(b''), tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('filename', ['../elsewhere.pdf', 'C:\\private\\receipt.pdf', 'NUL', '..', 'a/b.pdf'])
def test_suggested_filename_stays_inside_raw_dir(tmp_path, filename):
    d = Download(); d.suggested_filename = filename
    result = save_bundle(d, tmp_path)
    assert result.parent == tmp_path and result.read_bytes() == b'%PDF-test'


@pytest.mark.parametrize('exc,kind',[(TimeoutError(),'timeout'), (RuntimeError('canceled'),'cancelled'), (PermissionError(),'local_file_error')])
def test_failure_classification(exc,kind):
    assert error_kind(exc) == kind
