"""Favorite-attendee management and a searchable checkbox LOV for extra attendees."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, messagebox
from .attendee_favorites import FavoriteStore, split_people, validate_person, validate_people
from .ui_scroll import ScrollArea


class Dialog(tk.Toplevel):
    def __init__(self, parent, title, popover_anchor=None):
        self.previous_grab, self.previous_focus = parent.grab_current(), parent.focus_get()
        self.popover_anchor = popover_anchor
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        if popover_anchor is None:
            w, h = min(650, self.winfo_screenwidth()-40), min(620, self.winfo_screenheight()-90)
            self.geometry(f'{w}x{h}')
            self.minsize(min(w, 480), min(h, 380))
        else:
            self.overrideredirect(True)
            self.resizable(False, False)
            self.configure(borderwidth=1, relief='solid')
        self.columnconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda event: (self.close(), 'break')[1])

    def present(self):
        if self.popover_anchor is not None:
            self.update_idletasks()
            x, top, bottom, cell_width = self.popover_anchor
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            width = min(470, sw - 24)
            height = min(430, sh - 70)
            x = max(8, min(int(x), sw - width - 8))
            y = int(bottom) + 3
            if y + height > sh - 35:
                y = max(8, int(top) - height - 3)
            self.geometry(f'{width}x{height}+{x}+{y}')
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def close(self):
        self.destroy()
        for w, action in ((self.previous_grab, 'grab_set'), (self.previous_focus, 'focus_set')):
            try:
                if w is not None and w.winfo_exists():
                    getattr(w, action)()
            except tk.TclError:
                pass


class FavoritesManager(Dialog):
    def __init__(self, parent, store=None, on_saved=None):
        self.store = store if store is not None else FavoriteStore()
        super().__init__(parent, '자주 쓰는 추가 참석자 관리')
        self.people = deepcopy(self.store.people)
        self.on_saved, self.editing = on_saved, None
        self.rowconfigure(1, weight=1)
        head = ttk.Frame(self, padding=12); head.grid(row=0, column=0, sticky='ew')
        ttk.Label(head, text='표시 이름은 구분용, 검색값은 Concur에서 찾을 때 사용할 값입니다.\n이 목록은 이 PC에만 저장됩니다. 기존 작업지와 기본 참석자는 바뀌지 않습니다.',
                  wraplength=560).pack(anchor='w')
        area = ttk.Frame(self, padding=(12, 0)); area.grid(row=1, column=0, sticky='nsew')
        area.columnconfigure(0, weight=1); area.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(area, columns=('label', 'value'), show='headings', selectmode='browse')
        for key, title in [('label','표시 이름'), ('value','Concur 검색값')]:
            self.table.heading(key, text=title); self.table.column(key, width=230, minwidth=100)
        self.table.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(area, orient='vertical', command=self.table.yview)
        bar.grid(row=0, column=1, sticky='ns'); self.table.configure(yscrollcommand=bar.set)
        self.table.bind('<<TreeviewSelect>>', self.select)
        form = ttk.Frame(self, padding=12); form.grid(row=2, column=0, sticky='ew')
        form.columnconfigure(1, weight=1)
        self.label, self.value = tk.StringVar(self), tk.StringVar(self)
        for r, (title, var) in enumerate([('표시 이름',self.label), ('Concur 검색값', self.value)]):
            ttk.Label(form, text=title).grid(row=r, column=0, padx=(0,8), pady=4, sticky='w')
            ttk.Entry(form, textvariable=var).grid(row=r, column=1, sticky='ew')
        ttk.Label(form, text='검색값: 기존에 Concur 참석자 검색에 사용하던 이름 / 기업 이메일 등', wraplength=540).grid(row=2,column=0,columnspan=2,sticky='w',pady=6)
        actions=ttk.Frame(form); actions.grid(row=3,column=0,columnspan=2,sticky='w')
        for text,cmd in [('새 항목',self.new),('추가 / 수정',self.upsert),('선택 삭제',self.remove)]:
            ttk.Button(actions,text=text,command=cmd).pack(side='left',padx=(0,5))
        self.error=ttk.Label(form,wraplength=540); self.error.grid(row=4,column=0,columnspan=2,sticky='w',pady=6)
        foot=ttk.Frame(self,padding=12); foot.grid(row=3,column=0,sticky='ew')
        ttk.Button(foot,text='취소',command=self.close).pack(side='right')
        ttk.Button(foot,text='목록 저장',command=self.save).pack(side='right',padx=6)
        self.render(); self.present()

    def render(self):
        self.table.delete(*self.table.get_children())
        for i, person in enumerate(self.people):
            self.table.insert('', 'end', iid=str(i), values=(person['label'],person['value']))

    def select(self,event=None):
        selected=self.table.selection()
        if selected:
            self.editing=int(selected[0]); person=self.people[self.editing]
            self.label.set(person['label']); self.value.set(person['value'])

    def new(self):
        self.editing=None
        self.table.selection_remove(*self.table.selection())
        self.label.set(''); self.value.set(''); self.error.configure(text='새 참석자를 입력하세요.')

    def upsert(self):
        try:
            person=validate_person(self.label.get(),self.value.get())
            proposed=deepcopy(self.people)
            if self.editing is None: proposed.append(person)
            else: proposed[self.editing]=person
            self.people=validate_people(proposed)
        except ValueError as exc:
            self.error.configure(text=str(exc)); return False
        self.render(); self.new()
        self.error.configure(text='목록에 적용했습니다. 목록 저장을 눌러야 다음 실행에도 유지됩니다.')
        return True

    def remove(self):
        if self.editing is None: return
        if not messagebox.askyesno('목록에서 삭제','선택한 즐겨찾기를 삭제할까요? 기존 작업지의 참석자는 삭제되지 않습니다.',parent=self): return
        del self.people[self.editing]; self.render(); self.new()

    def save(self):
        # Do not silently discard a person typed into the form but not added yet.
        baseline=self.people[self.editing] if self.editing is not None else {'label':'','value':''}
        if (self.label.get().strip(), self.value.get().strip()) != (baseline['label'], baseline['value']):
            if not self.upsert(): return False
        try:
            self.store.save(self.people)
        except (OSError, ValueError) as exc:
            self.error.configure(text=f'저장하지 못했습니다: {exc}'); return False
        if self.on_saved: self.on_saved()
        self.close(); return True

    def close(self):
        baseline=self.people[self.editing] if self.editing is not None else {'label':'','value':''}
        pending=(self.label.get().strip(), self.value.get().strip()) != (baseline['label'],baseline['value'])
        if (pending or self.people != self.store.people) and not messagebox.askyesno(
                '목록 편집 취소','저장하지 않은 참석자 목록 변경을 버릴까요?',parent=self): return
        super().close()


class AttendeePicker(Dialog):
    def __init__(self,parent,current,on_apply,store=None,anchor=None):
        self.store=store if store is not None else FavoriteStore()
        super().__init__(parent,'추가 참석자 선택 · 여러 명 체크', popover_anchor=anchor)
        self.original, self.on_apply = current or '', on_apply
        self.options, self.checked = {}, {}
        selected=split_people(current)
        self.order=[s.casefold() for s in selected]
        # Preserve existing values (including unregistered or removed favorites).
        for value in selected: self.add_option(value,value,True)
        self.refresh_favorites()
        self.query=tk.StringVar(self)
        self.rowconfigure(1,weight=1)
        head=ttk.Frame(self,padding=12);head.grid(row=0,column=0,sticky='ew');head.columnconfigure(1,weight=1)
        ttk.Label(head,text='이름 / 검색값').grid(row=0,column=0,padx=(0,8))
        search=ttk.Entry(head,textvariable=self.query);search.grid(row=0,column=1,sticky='ew')
        ttk.Button(head,text='목록 관리',command=self.manage).grid(row=0,column=2,padx=(8,0))
        ttk.Label(head,text='체크한 사람만 추가 참석자 칸에 적용됩니다. 기존 입력도 목록에서 확인하세요.',wraplength=550).grid(row=1,column=0,columnspan=3,sticky='w',pady=(8,0))
        self.scroll=ScrollArea(self);self.scroll.grid(row=1,column=0,sticky='nsew',padx=8)
        foot=ttk.Frame(self,padding=12);foot.grid(row=2,column=0,sticky='ew');foot.columnconfigure(0,weight=1)
        self.count=ttk.Label(foot);self.count.grid(row=0,column=0,sticky='w')
        self.manual=tk.StringVar(self)
        ttk.Label(foot,text='이번 경비에만 직접 추가 (여러 명: 쉼표 구분)').grid(row=1,column=0,columnspan=2,sticky='w',pady=(8,2))
        ttk.Entry(foot,textvariable=self.manual).grid(row=2,column=0,sticky='ew')
        ttk.Button(foot,text='추가',command=self.add_manual).grid(row=2,column=1,padx=6)
        actions=ttk.Frame(foot);actions.grid(row=3,column=0,columnspan=2,sticky='ew',pady=8)
        for text,cmd in [('표시 항목 선택',lambda:self.check_shown(True)),('표시 항목 해제',lambda:self.check_shown(False))]:
            ttk.Button(actions,text=text,command=cmd).pack(side='left',padx=(0,5))
        ttk.Button(actions,text='취소',command=self.close).pack(side='right')
        ttk.Button(actions,text='적용',command=self.apply).pack(side='right',padx=5)
        ttk.Label(foot,text='빈칸은 기존 Concur 값 유지 · 여기서 Concur 참석자를 등록/삭제하지 않습니다.',wraplength=550).grid(row=4,column=0,columnspan=2,sticky='w')
        self.query.trace_add('write',lambda *a:self.render())
        self.render();self.present();search.focus_set()

    def add_option(self,value,label,selected=False):
        key=value.casefold()
        if key not in self.options:
            self.options[key]={'label':label,'value':value,'registered':False}
            self.checked[key]=tk.BooleanVar(self,value=selected)
        return key

    def refresh_favorites(self):
        for item in self.options.values(): item['registered']=False
        for item in self.store.people:
            key=self.add_option(item['value'],item['label'])
            self.options[key].update(label=item['label'],registered=True)
        # Preserve any already checked value even after a favorite is deleted/edited.

    def render(self):
        for child in self.scroll.body.winfo_children(): child.destroy()
        q=self.query.get().strip().casefold()
        self.shown=[k for k,p in self.options.items() if q in (p['label']+' '+p['value']).casefold()]
        for k in self.shown:
            p=self.options[k]
            suffix='' if p['registered'] else ' · 미등록 / 이번 경비만'
            ttk.Checkbutton(self.scroll.body,text=f"{p['label']}  ({p['value']}){suffix}",
                variable=self.checked[k],command=self.update_count).pack(anchor='w',fill='x',pady=5)
        if not self.shown:
            ttk.Label(self.scroll.body,text='등록된 참석자가 없습니다. 목록 관리에서 추가하세요.' if not self.options else '검색 결과가 없습니다.').pack(anchor='w')
        self.scroll.enable_children();self.update_count()

    def update_count(self):
        self.count.configure(text=f'선택 {sum(v.get() for v in self.checked.values())}명 · 표시 {len(self.shown)} / 전체 {len(self.options)}명')

    def check_shown(self, value):
        for key in self.shown: self.checked[key].set(value)
        self.update_count()

    def add_manual(self):
        values=split_people(self.manual.get())
        try:
            for value in values: validate_person('',value)
        except ValueError as exc:
            self.count.configure(text=str(exc));return False
        for value in values:
            k=self.add_option(value,value,True);self.checked[k].set(True)
        self.manual.set('');self.render();return True

    def manage(self):
        def refresh():
            self.refresh_favorites();self.render()
        return FavoritesManager(self,self.store,on_saved=refresh)

    def apply(self):
        if self.manual.get().strip() and not self.add_manual(): return False
        order=list(dict.fromkeys([*self.order,*self.options]))
        values=[self.options[k]['value'] for k in order if self.checked[k].get()]
        current=split_people(self.original)
        result=self.original if values==current else ', '.join(values)
        if self.on_apply(result) is False: return False
        self.close();return True


def show_manager(parent):
    try: return FavoritesManager(parent)
    except (OSError,ValueError) as exc:
        messagebox.showerror('참석자 목록 확인',f'목록을 열지 못했습니다. 원본 파일은 바꾸지 않았습니다.\n{exc}',parent=parent)


def show_picker(parent,current,on_apply,anchor=None):
    try: return AttendeePicker(parent,current,on_apply,anchor=anchor)
    except (OSError,ValueError) as exc:
        messagebox.showerror('참석자 목록 확인',f'목록을 열지 못했습니다. 직접 입력은 계속 사용할 수 있습니다.\n{exc}',parent=parent)


class AttendeesEntry(ttk.Frame):
    def __init__(self,parent,variable):
        super().__init__(parent)
        self.variable=variable
        self.columnconfigure(0,weight=1)
        self.entry=ttk.Entry(self,textvariable=variable)
        self.entry.grid(row=0,column=0,sticky='ew')
        ttk.Button(self,text='선택 ▾',command=self.open).grid(row=0,column=1,padx=(5,0))
        for key in ('<Alt-Down>','<F4>'):
            self.entry.bind(key,lambda e:(self.open(),'break')[1])

    def open(self):
        return show_picker(self,self.variable.get(),lambda value:self.variable.set(value))
