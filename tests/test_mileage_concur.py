from types import SimpleNamespace

from PIL import Image

from src.concur_formats import using_formats
from src.mileage import MileageBook, new_row
from src.mileage_concur import BindingStore, _date_text, row_fingerprint, run


def ready_book(tmp_path):
    source = tmp_path / 'map.png'
    Image.new('RGB', (20, 20)).save(source)
    book = MileageBook(tmp_path)
    row = {**new_row(), 'date':'2026-09-18', 'origin':'출발', 'destination':'도착',
           'vehicle':'My Car', 'kind':'long', 'rate':'470', 'distance':'10.5',
           'passengers':'2', 'description':'업무 방문', **book.attach_image(source)}
    book.commit_rows([row]); book.save()
    return book, book.rows[0]


class Page:
    def __init__(self):
        self.uploads = []
    def set_input_files(self, selector, path):
        self.uploads.append((selector, path))
    def wait_for_timeout(self, ms):
        pass


def report():
    return SimpleNamespace(url='https://example.concursolutions.com/nui/expense/reports/R',
                           key=('example.concursolutions.com','R'))


def test_date_format_uses_explicit_concur_order():
    day = __import__('datetime').date(2026, 9, 18)
    with using_formats({'concur_date_order':'YMD','concur_number_style':'DOT'}):
        assert _date_text(day, '', '') == '2026-09-18'
    with using_formats({'concur_date_order':'MDY','concur_number_style':'DOT'}):
        assert _date_text(day, '', '') == '09/18/2026'


def test_binding_store_roundtrip(tmp_path):
    store = BindingStore(tmp_path)
    store.set('L1', report='R', fingerprint='F', expense_id='E1', state='bound')
    assert BindingStore(tmp_path).get('L1')['expense_id'] == 'E1'


def test_verified_mileage_is_not_created_or_uploaded_twice(tmp_path, monkeypatch):
    book, row = ready_book(tmp_path)
    page, rep = Page(), report()
    calls = {'create':0, 'fill':0, 'save':0, 'verify':0}

    def create(page_, report_, store, row_):
        calls['create'] += 1
        store.set(row_['id'], report=report_.url, fingerprint=row_fingerprint(row_),
                  expense_id='E1', state='bound')
        return 'E1'
    monkeypatch.setattr('src.mileage_concur._open_new', create)
    monkeypatch.setattr('src.mileage_concur._apply_fields', lambda *a: calls.__setitem__('fill', calls['fill'] + 1))
    monkeypatch.setattr('src.mileage_concur._verify',
                        lambda *a: (calls.__setitem__('verify', calls['verify'] + 1) or (True, True)))
    monkeypatch.setattr('src.mileage_concur.fx._save_expense',
                        lambda *a, **k: calls.__setitem__('save', calls['save'] + 1))

    assert int(run(page, rep, tmp_path, True)) == 0
    assert calls['create'] == calls['fill'] == calls['save'] == 1
    assert len(page.uploads) == 1
    assert BindingStore(tmp_path).get(row['id'])['state'] == 'verified'

    assert int(run(page, rep, tmp_path, True)) == 0
    assert calls['create'] == calls['fill'] == calls['save'] == 1
    assert len(page.uploads) == 1
    assert calls['verify'] >= 2


def test_uncertain_save_is_read_only_on_rerun(tmp_path, monkeypatch):
    book, row = ready_book(tmp_path)
    store = BindingStore(tmp_path)
    store.set(row['id'], report=report().url, fingerprint=row_fingerprint(row),
              expense_id='E1', state='saving')
    monkeypatch.setattr('src.mileage_concur._verify', lambda *a: (False, None))
    monkeypatch.setattr('src.mileage_concur._open_new',
                        lambda *a: (_ for _ in ()).throw(AssertionError('must not create')))
    monkeypatch.setattr('src.mileage_concur._apply_fields',
                        lambda *a: (_ for _ in ()).throw(AssertionError('must not write')))
    monkeypatch.setattr('src.mileage_concur.fx._save_expense',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not save')))
    page = Page()
    assert int(run(page, report(), tmp_path, True)) == 1
    assert page.uploads == []
    assert BindingStore(tmp_path).get(row['id'])['state'] == 'needs_review'


def test_preview_never_creates_binding_or_upload(tmp_path):
    ready_book(tmp_path)
    page = Page()
    result = run(page, report(), tmp_path, False)
    assert int(result) == 0 and '미리보기' in result.summary
    assert not (tmp_path / 'mileage-concur.json').exists()
    assert page.uploads == []


def test_uncertain_create_without_id_never_retries(tmp_path, monkeypatch):
    book, row = ready_book(tmp_path)
    store = BindingStore(tmp_path)
    store.set(row['id'], report=report().url, fingerprint=row_fingerprint(row),
              expense_id=None, state='needs_review')
    monkeypatch.setattr('src.mileage_concur._open_new',
                        lambda *a: (_ for _ in ()).throw(AssertionError('must not create again')))
    page = Page()
    assert int(run(page, report(), tmp_path, True)) == 1
    assert page.uploads == []
    assert BindingStore(tmp_path).get(row['id'])['state'] == 'needs_review'
