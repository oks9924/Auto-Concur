"""Calendar UI, keyboard, draft safety and expense guide regression tests."""
from datetime import date
from types import SimpleNamespace
import tkinter as tk
import pytest
from src.calendar_widgets import CalendarPanel, shift_month, date_text, period_preset
from src.calendar_input import DatePicker, DateEntry, RangePicker
from src.stay_calendar import StayCalendar
from src.expense_policy import input_guide


@pytest.fixture
def root(tk_window):
    tk_window.geometry('900x650')
    return tk_window


@pytest.mark.parametrize('start,delta,result', [
    (date(2026,1,31),1,date(2026,2,28)), (date(2028,1,31),1,date(2028,2,29)),
    (date(2026,12,31),1,date(2027,1,31)), (date(2026,1,1),-1,date(2025,12,1)),
    (date(1,1,1),-1,date(1,1,1)), (date(9999,12,31),1,date(9999,12,31))])
def test_month_navigation(start, delta, result):
    assert shift_month(start, delta) == result


@pytest.mark.parametrize('name,expected', [
    ('오늘',(date(2026,9,17),date(2026,9,17))),
    ('최근 7일',(date(2026,9,11),date(2026,9,17))),
    ('이번 달',(date(2026,9,1),date(2026,9,17))),
    ('지난달',(date(2026,8,1),date(2026,8,31)))])
def test_shortcuts(name, expected):
    assert period_preset(name,date(2026,9,17)) == expected


def test_last_month_year_and_leap_boundaries():
    assert period_preset('지난달',date(2026,1,1)) == (date(2025,12,1),date(2025,12,31))
    assert period_preset('지난달',date(2028,3,1))[-1] == date(2028,2,29)
    assert date_text(date(1,1,1)) == '0001-01-01'


def test_year_month_jump_and_invalid_input(root):
    panel = CalendarPanel(root,date(2026,9,17),lambda value:None)
    panel.year_var.set('2028'); panel.month_var.set('2'); panel.jump()
    assert (panel.year,panel.month)==(2028,2)
    panel.year_var.set('bad'); panel.jump()
    assert panel.year_var.get() == '2028'
    panel.year_var.set('0'); panel.jump()
    assert panel.year_var.get() == '2028'


@pytest.mark.parametrize('key,shift,expected', [
    ('Left',0,date(2026,9,16)), ('Right',0,date(2026,9,18)),
    ('Up',0,date(2026,9,10)), ('Down',0,date(2026,9,24)),
    ('Home',0,date(2026,9,14)), ('End',0,date(2026,9,20)),
    ('Prior',0,date(2026,8,17)), ('Next',0,date(2026,10,17)),
    ('Prior',1,date(2025,9,17)), ('Next',1,date(2027,9,17))])
def test_keyboard_moves_without_committing(root,key,shift,expected):
    selected=[]
    panel=CalendarPanel(root,date(2026,9,17),selected.append)
    panel.key(SimpleNamespace(keysym=key,state=shift),date(2026,9,17))
    assert panel.cursor==expected and selected==[]
    assert sum(int(b.cget('takefocus')) for b in panel.buttons.values())==1
    panel.key(SimpleNamespace(keysym='Return',state=0),expected)
    assert selected==[expected]


def test_date_entry_typing_validation_and_trace_cleanup(root):
    v=tk.StringVar(root,'20260917'); field=DateEntry(root,v)
    field.normalize(); assert v.get()=='2026.09.17'
    v.set('2026-02-30');field.normalize();assert field.entry.instate(['invalid'])
    field.open();field.dialog.select(date(2028,2,29));assert v.get()=='2028.02.29'
    assert not field.entry.instate(['invalid'])
    field.destroy();v.set('2026-09-17');assert not v.trace_info()


def test_period_cancel_and_same_day(root):
    applied=[]
    dialog=RangePicker(root,date(2026,9,17),None,None,lambda *values:applied.append(values))
    dialog.choose_date(date(2026,9,17));dialog.choose_date(date(2026,9,17))
    assert not applied
    dialog.close();assert not applied
    dialog=RangePicker(root,date(2026,9,17),date(2026,9,17),date(2026,9,17),lambda *x:applied.append(x))
    dialog.apply();assert applied==[('2026-09-17','2026-09-17')]


def test_stay_incomplete_invalid_and_shortcut(root):
    applied=[]
    dialog=StayCalendar(root,date(2026,9,17),None,None,lambda *x:applied.append(x))
    dialog.choose_date(date(2026,9,17));assert dialog.mode.get()=='퇴실'
    dialog.apply();assert not applied and dialog.winfo_exists()
    dialog.choose_date(date(2026,9,17));dialog.apply();assert not applied
    dialog.preset('2박');dialog.apply()
    assert applied==[('2026-09-17','2026-09-19')]


def test_range_typing_and_clear_are_draft_only(root):
    applied=[]
    dialog=StayCalendar(root,date(2026,12,31),None,None,lambda *x:applied.append(x))
    dialog.start_var.set('2026-12-31');dialog.end_var.set('2027-01-02');dialog.manual()
    assert dialog.end==date(2027,1,2) and not applied
    dialog.end_var.set('bad');dialog.apply();assert not applied
    dialog.clear();assert not applied
    dialog.apply();assert applied==[('','')]


def test_previous_grab_and_focus_restored(root):
    window=tk.Toplevel(root); entry=tk.Entry(window);entry.pack();root.update()
    window.grab_set();entry.focus_force();root.update()
    dialog=StayCalendar(window,date(2026,9,17),None,None,lambda *a:None)
    dialog.close();root.update()
    assert root.grab_current()==window
    assert root.focus_get()==entry
    window.destroy()


def test_same_component_for_all_calendars(root):
    single=DatePicker(root,date(2026,9,17),lambda value:None)
    assert isinstance(single.panel,CalendarPanel);single.close()
    stay=StayCalendar(root,date(2026,9,17),None,None,lambda *x:None)
    assert isinstance(stay.panel,CalendarPanel);stay.close()


@pytest.mark.parametrize('name,code,count,known', [
    ('숙박비',None,5,True), ('다른 숙박 표시명','LODNG',5,True),
    ('내부 직원간 식음료',None,4,True), ('대중교통비', 'TRAIN',1,True),
    ('주차비','PARKG',1,True), ('렌터카비','CARRT',0,False),
    ('알 수 없는 식음료','UNKNOWN',0,False), ('',None,0,False)])
def test_guide_does_not_guess_required_fields(name,code,count,known):
    fields,registered=input_guide(name,code)
    assert len(fields)==count and registered==known


def test_editor_unknown_guide_and_bulk_calendar(root,tmp_path):
    from src import settings,organize
    from src.worksheet import write_json
    from src.worksheet_editor import Editor
    base={**dict.fromkeys(organize.MANIFEST_COLUMNS,''),'거래일':'2026-09-17','금액':'1000','승인번호':'A','가맹점명':'test','파일명':'a.pdf','경비유형':'렌터카비'}
    path=tmp_path/'workbook.json';write_json(path,{'version':1,'rows':[base]})
    editor=Editor(root,path,settings.DEFAULTS)
    assert '미등록 1건' in editor.guide_summary.get()
    editor.filter.set('안내 미등록');assert editor.table.display_rows()==[0]
    editor.table.select_cell(0,4);editor.bulk();root.update()
    popup=[w for w in editor.winfo_children() if isinstance(w,tk.Toplevel)][-1]
    fields=[]
    def walk(w):
        fields.extend([w] if isinstance(w,DateEntry) else [])
        for child in w.winfo_children():walk(child)
    walk(popup);assert len(fields)==1
    popup.destroy()
    if editor.pending:editor.after_cancel(editor.pending)
    editor.destroy()



def test_selection_change_reuses_existing_calendar_buttons(root):
    panel=CalendarPanel(root,date(2026,9,17),lambda value:None)
    before={day:id(button) for day,button in panel.buttons.items()}
    panel.set_selection(date(2026,9,10),date(2026,9,12))
    after={day:id(button) for day,button in panel.buttons.items()}
    assert after==before
    assert panel.buttons[date(2026,9,10)].cget('bg')=='#14634b'


def test_month_navigation_reuses_calendar_button_widgets(root):
    panel=CalendarPanel(root,date(2026,9,17),lambda value:None)
    before=[id(button) for button in panel._slots[0]['cells']]
    panel.move(1)
    after=[id(button) for button in panel._slots[0]['cells']]
    assert after==before
    assert panel.month==10 and date(2026,10,17) in panel.buttons


def test_range_picker_uses_one_month_for_faster_open(root):
    dialog=RangePicker(root,date(2026,9,17),None,None,lambda *x:None,stay=True)
    assert dialog.panel.months==1
    assert len(dialog.panel._slots)==1
    dialog.close()
