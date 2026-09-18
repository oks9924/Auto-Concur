"""Task-oriented desktop layout. No browser or expense mutation logic lives here."""
import tkinter as tk
from tkinter import ttk, scrolledtext
from . import paths
from .calendar_input import DateEntry, initial_period
from .concur_formats import show_settings
from .result_viewer import ResultViewer
from .ui_scroll import ScrollArea
from .attendee_picker import show_manager


def configure_styles(root):
    style = ttk.Style(root)
    if 'clam' in style.theme_names():
        style.theme_use('clam')
    root.configure(background='#f5f7fb')
    style.configure('TFrame', background='#f5f7fb')
    style.configure('TLabel', background='#f5f7fb', foreground='#253447')
    style.configure('TLabelframe', background='#f5f7fb', bordercolor='#d6dfea')
    style.configure('TLabelframe.Label', background='#f5f7fb', foreground='#253447')
    style.configure('TButton', padding=(9, 5), background='#ffffff', foreground='#233951')
    style.map('TButton', background=[('active', '#e8f0f9')])
    style.configure('TNotebook', background='#f5f7fb', borderwidth=0)
    style.configure('TNotebook.Tab', padding=(12, 7))
    style.configure('Treeview', rowheight=30)
    style.configure('UX.Title.TLabel', font=('맑은 고딕', 20, 'bold'))
    style.configure('UX.Subtitle.TLabel', foreground='#536273')
    style.configure('UX.Card.TLabelframe.Label', font=('맑은 고딕', 12, 'bold'))
    style.configure('UX.Primary.TButton', font=('맑은 고딕', 11, 'bold'), padding=(12, 8))
    style.configure('UX.Action.TButton', padding=(8, 6))
    style.configure('UX.Status.TLabel', font=('맑은 고딕', 11, 'bold'), foreground='#173e65')


def build_workspace(app):
    configure_styles(app)
    width = min(1080, max(620, app.winfo_screenwidth() - 50))
    height = min(840, max(500, app.winfo_screenheight() - 90))
    app.geometry(f'{width}x{height}')
    app.minsize(min(700, width), min(530, height))
    app.columnconfigure(0, weight=1)
    app.rowconfigure(2, weight=1)
    app.buttons, app.inputs = [], []
    app.run_state = tk.StringVar(value='대기 · 기존 작업지가 있으면 ② 경비 입력부터 이어서 진행하세요.')
    app.current_activity = ''
    header = ttk.Frame(app, padding=(18, 12, 18, 6))
    header.grid(row=0, column=0, sticky='ew')
    ttk.Label(header, text='Auto-Concur', style='UX.Title.TLabel').pack(anchor='w')
    ttk.Label(header, text='전표 준비  →  경비 입력  →  Concur 반영  →  결과 확인',
              style='UX.Subtitle.TLabel').pack(anchor='w', pady=(4, 0))
    status = ttk.Frame(app, padding=(18, 4, 18, 8))
    status.grid(row=1, column=0, sticky='ew')
    app.activity_label = ttk.Label(status, textvariable=app.run_state, style='UX.Status.TLabel', wraplength=950)
    app.activity_label.pack(fill='x')
    status.bind('<Configure>', lambda event: app.activity_label.configure(wraplength=max(300, event.width - 40)))
    app.activity = ttk.Progressbar(status, mode='indeterminate')
    app.tabs = ttk.Notebook(app)
    app.tabs.grid(row=2, column=0, sticky='nsew', padx=14, pady=(0, 12))
    area = ScrollArea(app.tabs)
    home = area.body
    app.home_scroll = area
    app.tabs.add(area, text='  작업 공간  ')
    home.columnconfigure(0, weight=1)
    home.rowconfigure(2, weight=1)
    config = ttk.LabelFrame(home, text='작업 설정', padding=10)
    config.grid(row=0, column=0, sticky='ew', pady=(0, 10))
    config.columnconfigure(1, weight=1)
    app.folder = tk.StringVar(value=str(paths.folder(app.cfg.get('downloads_dir') or '')))
    ttk.Label(config, text='전표 폴더').grid(row=0, column=0, sticky='w', padx=(0, 10))
    folder_entry = ttk.Entry(config, textvariable=app.folder)
    folder_entry.grid(row=0, column=1, sticky='ew')
    pick = ttk.Button(config, text='찾아보기', command=app.pick_folder)
    pick.grid(row=0, column=2, padx=(6, 0))
    app.buttons.append(pick)
    app.inputs.append(folder_entry)
    first, last = initial_period(app.cfg)
    app.from_date, app.to_date = tk.StringVar(value=first), tk.StringVar(value=last)
    ttk.Label(config, text='조회 기간').grid(row=1, column=0, sticky='w', pady=8)
    dates = ttk.Frame(config)
    dates.grid(row=1, column=1, columnspan=2, sticky='w', pady=8)
    for label, variable in [('시작일', app.from_date), ('종료일', app.to_date)]:
        ttk.Label(dates, text=label).pack(side='left', padx=(0, 4))
        entry = DateEntry(dates, variable, f'조회 {label} 선택')
        entry.pack(side='left', padx=(0, 12))
        app.inputs.append(entry.entry)
        app.buttons.append(entry.button)
    quick = ttk.Frame(config)
    quick.grid(row=2, column=1, columnspan=2, sticky='w')
    for name, cmd in [('기간 선택', app.pick_period), *[(n, lambda n=n: app.set_period(n)) for n in ('오늘', '최근 7일', '이번 달', '지난달')]]:
        button = ttk.Button(quick, text=name, command=cmd)
        button.pack(side='left', padx=(0, 4))
        app.buttons.append(button)
    app.attendee = tk.StringVar(value=str(app.cfg.get('attendee_default', '')))
    ttk.Label(config, text='기본 참석자').grid(row=3, column=0, sticky='w', pady=(8, 0))
    who = ttk.Entry(config, textvariable=app.attendee)
    who.grid(row=3, column=1, sticky='ew', pady=(8, 0))
    app.inputs.append(who)
    ttk.Label(config, text='여러 명: 쉼표로 구분', style='UX.Subtitle.TLabel').grid(row=3, column=2, padx=6, pady=(8, 0))

    favorites = ttk.Button(config, text='자주 쓰는 추가 참석자 관리', command=lambda: show_manager(app))
    favorites.grid(row=4, column=1, sticky='w', pady=(8, 0))
    app.buttons.append(favorites)

    cards = ttk.Frame(home)
    cards.grid(row=1, column=0, sticky='ew')
    cards.columnconfigure((0, 1), weight=1, uniform='card')
    specs = [
        ('① 전표 준비', '카드 전표를 받아 작업지를 만듭니다.', [('전표 다운로드', app.step_download), ('작업지 생성', app.step_organize)]),
        ('② 경비 입력', '기존 작업지는 이 단계에서 바로 이어갑니다.', [('작업지 열기', app.edit_worksheet)]),
        ('③ Concur 반영', '카드 경비 반영 · 마일리지 신규 생성은 아직 미연결입니다.', [('Concur 반영', app.step_update)]),
        ('④ 결과 확인', '과거 검증 기록과 이번 실행의 로그를 구분합니다.', [('검증 기록 보기', app.show_results), ('상세 로그', lambda: app.tabs.select(app.log_tab))]),
    ]
    for i, (title, description, actions) in enumerate(specs):
        card = ttk.LabelFrame(cards, text=title, style='UX.Card.TLabelframe', padding=10)
        card.grid(row=i // 2, column=i % 2, sticky='nsew', padx=(0, 6) if i % 2 == 0 else (6, 0), pady=6)
        label = ttk.Label(card, text=description, wraplength=420, style='UX.Subtitle.TLabel')
        label.pack(anchor='w', fill='x')
        card.bind('<Configure>', lambda e, label=label: label.configure(wraplength=max(150, e.width - 30)))
        action_bar = ttk.Frame(card)
        action_bar.pack(fill='x', pady=(10, 0))
        for name, command in actions:
            b = ttk.Button(action_bar, text=name, command=command, style='UX.Action.TButton')
            b.pack(side='left', padx=(0, 5))
            app.buttons.append(b)
    extras = ttk.Frame(home)
    extras.grid(row=3, column=0, sticky='ew', pady=(8, 0))
    app.limit = tk.StringVar(value='')
    ttk.Label(extras, text='앞 N건만 (선택)').pack(side='left')
    limit = ttk.Entry(extras, textvariable=app.limit, width=5)
    limit.pack(side='left', padx=5)
    app.inputs.append(limit)
    for name, command in [('Concur 표시 서식', lambda: show_settings(app))]:
        b = ttk.Button(extras, text=name, command=command)
        b.pack(side='left', padx=4)
        app.buttons.append(b)
    more = ttk.Menubutton(extras, text='고급 도구')
    menu = tk.Menu(more, tearoff=False)
    menu.add_command(label='경비유형 코드 확인', command=app.step_list_types)
    menu.add_command(label='숙박비 목록 확인', command=app.step_list_lodging)
    more.configure(menu=menu)
    more.pack(side='left', padx=4)
    app.buttons.append(more)
    app.result_tab = ResultViewer(app.tabs, lambda: paths.folder(app.folder.get().strip()))
    app.tabs.add(app.result_tab, text='  결과 기록  ')
    app.log_tab = ttk.Frame(app.tabs, padding=10)
    app.tabs.add(app.log_tab, text='  상세 로그  ')
    ttk.Label(app.log_tab, text='현재 실행의 전체 기록 · 확인이 필요한 문구는 복사해서 공유할 수 있습니다.',
              wraplength=700).pack(anchor='w')
    app.log = scrolledtext.ScrolledText(app.log_tab, width=80, height=12, state='disabled', wrap='word')
    app.log.pack(fill='both', expand=True, pady=8)
    app.log.tag_configure('강조', font=('맑은 고딕', 10, 'bold'), foreground='#17603b')
    ttk.Button(app.log_tab, text='로그 전체 복사', command=app.copy_log).pack(anchor='e')
    app._say(f'코드 기준: {paths.stamp()}\n기존 작업지가 있으면 다시 생성하지 않고 ② 경비 입력에서 열 수 있습니다.\n')
    area.enable_children()
    app.protocol('WM_DELETE_WINDOW', app.close_window)


def set_busy(app, busy, message):
    app.run_state.set(message)
    if busy:
        app.activity.pack(fill='x', pady=(6, 0))
        app.activity.start(15)
        app.tabs.select(app.log_tab)
    else:
        app.activity.stop()
        app.activity.configure(value=0)
        app.activity.pack_forget()
    for widget in app.inputs:
        widget.state(['disabled'] if busy else ['!disabled'])
