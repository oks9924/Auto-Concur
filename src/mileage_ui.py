"""Manual mileage table below the original card worksheet, with explicit Concur create handoff."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .attendee_picker import Dialog
from .calendar_input import DateEntry
from .ui_scroll import ScrollArea
from .mileage import RATES, MileageBook, checked_row, new_row, vehicles_store, validate_vehicles


class VehicleManager(Dialog):
    def __init__(self, parent, on_saved=None):
        self.store = vehicles_store()
        super().__init__(parent, '차량 ID 관리 · 마일리지')
        self.rows, self.editing, self.on_saved = deepcopy(self.store.rows), None, on_saved
        self.rowconfigure(1, weight=1)
        ttk.Label(self, text='Concur 차량 ID에 본인이 지정한 이름을 정확히 등록하세요.\nlong 470원/km · short 280원/km (사용자 제공 기준, 실제 Concur 환급률 별도 확인)',
                  wraplength=560, padding=12).grid(row=0, column=0, sticky='ew')
        self.table = ttk.Treeview(self, columns=('vehicle', 'kind', 'rate'), show='headings', selectmode='browse')
        for key, title in [('vehicle','Concur 차량 ID'), ('kind','구분'), ('rate','환급률(원/km)')]:
            self.table.heading(key,text=title); self.table.column(key,width=160,minwidth=70)
        self.table.grid(row=1,column=0,sticky='nsew',padx=12)
        self.table.bind('<<TreeviewSelect>>',self.select)
        box=ttk.Frame(self,padding=12);box.grid(row=2,column=0,sticky='ew');box.columnconfigure(1,weight=1)
        self.vehicle=tk.StringVar(self);self.kind=tk.StringVar(self,value='long')
        ttk.Label(box,text='차량 ID').grid(row=0,column=0,padx=(0,8))
        ttk.Entry(box,textvariable=self.vehicle).grid(row=0,column=1,sticky='ew')
        ttk.Combobox(box,textvariable=self.kind,values=list(RATES),state='readonly',width=10).grid(row=0,column=2,padx=6)
        actions=ttk.Frame(box);actions.grid(row=1,column=0,columnspan=3,sticky='ew',pady=10)
        for text,command in [('새 차량',self.new),('추가 / 수정',self.upsert),('선택 삭제',self.remove),('목록 저장',self.save)]:
            ttk.Button(actions,text=text,command=command).pack(side='left',padx=3)
        self.error=ttk.Label(box,wraplength=550);self.error.grid(row=2,column=0,columnspan=3,sticky='w')
        self.render();self.present()

    def render(self):
        self.table.delete(*self.table.get_children())
        for i,r in enumerate(self.rows): self.table.insert('','end',iid=str(i),values=(r['vehicle'],r['kind'],r['rate']))

    def select(self,event=None):
        selected=self.table.selection()
        if selected:
            self.editing=int(selected[0]);r=self.rows[self.editing]
            self.vehicle.set(r['vehicle']);self.kind.set(r['kind'])

    def new(self):
        self.editing=None;self.vehicle.set('');self.kind.set('long')
        self.table.selection_remove(*self.table.selection())

    def upsert(self):
        rows=deepcopy(self.rows);value={'vehicle':self.vehicle.get(),'kind':self.kind.get()}
        if self.editing is None: rows.append(value)
        else: rows[self.editing]=value
        try: self.rows=validate_vehicles(rows)
        except ValueError as exc: self.error.configure(text=str(exc));return False
        self.render();self.new();self.error.configure(text='목록 저장을 누르면 다음 실행에도 유지됩니다.');return True

    def remove(self):
        if self.editing is None: return
        if messagebox.askyesno('차량 삭제','등록 목록에서만 삭제합니다. 기존 마일리지 행은 보존합니다. 삭제할까요?',parent=self):
            del self.rows[self.editing];self.render();self.new()

    def save(self):
        if self.vehicle.get().strip():
            if not self.upsert(): return False
        self.store.rows=deepcopy(self.rows)
        try: self.store.save()
        except (ValueError,OSError) as exc: self.error.configure(text=str(exc));return False
        if self.on_saved: self.on_saved()
        super().close();return True

    def close(self):
        if (self.rows!=self.store.original or self.vehicle.get().strip()) and not messagebox.askyesno(
                '차량 목록 닫기','저장하지 않은 입력은 버리고 닫을까요?',parent=self): return
        super().close()


class MileageForm(Dialog):
    def __init__(self, panel, index=None):
        super().__init__(panel,'차량 마일리지 추가' if index is None else '차량 마일리지 수정')
        self.panel,self.index=panel,index
        self.original=deepcopy(panel.book.rows[index]) if index is not None else new_row()
        self.snapshot=deepcopy(panel.book.rows)
        self.map_fields={k:v for k,v in self.original.items() if k.startswith('map')}
        self.chosen_image=None
        self.rowconfigure(0,weight=1)
        area=ScrollArea(self);area.grid(row=0,column=0,sticky='nsew')
        box=area.body;box.columnconfigure(1,weight=1)
        self.vars={k:tk.StringVar(self,value=str(self.original.get(k,''))) for k in
                   ('date','origin','destination','vehicle','distance','passengers')}
        if not self.vars['date'].get(): self.vars['date'].set(date.today().isoformat())
        self.vehicle_records=[]
        fields=[('거래 날짜','date'),('출발지','origin'),('도착지','destination'),('차량 ID','vehicle'),('거리 (km)','distance'),('탑승자 수','passengers')]
        for i,(label,key) in enumerate(fields):
            ttk.Label(box,text=label).grid(row=i,column=0,sticky='w',padx=(0,10),pady=6)
            if key=='date': widget=DateEntry(box,self.vars[key],'마일리지 거래 날짜',separator='-')
            elif key=='vehicle':
                frame=ttk.Frame(box);frame.columnconfigure(0,weight=1)
                self.vehicles=ttk.Combobox(frame,textvariable=self.vars[key],state='readonly')
                self.vehicles.grid(row=0,column=0,sticky='ew')
                ttk.Button(frame,text='차량 관리',command=self.manage).grid(row=0,column=1,padx=(5,0));widget=frame
            else: widget=ttk.Entry(box,textvariable=self.vars[key])
            widget.grid(row=i,column=1,sticky='ew',pady=6)
        ttk.Label(box,text='탑승자 수는 Concur의 입력 기준으로 지정하세요. 예상금액에 곱하지 않습니다.',wraplength=510).grid(row=6,column=0,columnspan=2,sticky='w')
        self.estimate=ttk.Label(box,wraplength=510);self.estimate.grid(row=7,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(box,text='설명').grid(row=8,column=0,sticky='nw',pady=6)
        self.description=tk.Text(box,height=3,width=30,wrap='word');self.description.insert('1.0',self.original.get('description',''))
        self.description.grid(row=8,column=1,sticky='ew',pady=6)
        ttk.Button(box,text='지도 이미지 첨부',command=self.pick_image).grid(row=9,column=0,sticky='w',pady=6)
        self.map_label=ttk.Label(box,text=self.map_fields.get('map_name') or 'PNG / JPG · 20MB 이하',wraplength=370)
        self.map_label.grid(row=9,column=1,sticky='w')
        ttk.Label(box,text='지도는 실제 경로 이미지 파일을 선택하세요. 도구가 경로나 증빙을 생성하지 않습니다.\n표에 저장한 뒤 마일리지 영역의 [Concur 신규 생성]에서 별도 실행합니다.',wraplength=520).grid(row=10,column=0,columnspan=2,sticky='w',pady=6)
        foot=ttk.Frame(self,padding=12);foot.grid(row=1,column=0,sticky='ew')
        self.error=ttk.Label(foot,wraplength=540);self.error.pack(anchor='w')
        ttk.Button(foot,text='취소',command=self.close).pack(side='right')
        ttk.Button(foot,text='마일리지 표에 적용',command=self.apply).pack(side='right',padx=6)
        self.vars['vehicle'].trace_add('write',lambda *a:self.update_estimate())
        self.vars['distance'].trace_add('write',lambda *a:self.update_estimate())
        self.reload_vehicles();area.enable_children();self.present()

    def reload_vehicles(self):
        self.vehicle_records=vehicles_store().rows
        self.vehicles.configure(values=list(dict.fromkeys([*[r['vehicle'] for r in self.vehicle_records],
                                    *([self.original['vehicle']] if self.original.get('vehicle') else [])])))
        self.update_estimate()

    def manage(self):
        try: return VehicleManager(self,self.reload_vehicles)
        except (ValueError,OSError) as exc: self.error.configure(text=str(exc))

    def selected_vehicle(self):
        value=self.vars['vehicle'].get()
        # Existing rows retain their original rate even when vehicle settings change.
        if value==self.original.get('vehicle'): return {k:self.original[k] for k in ('vehicle','kind','rate')}
        return next((r for r in self.vehicle_records if r['vehicle']==value),None)

    def update_estimate(self):
        row=self.selected_vehicle()
        if not row: self.estimate.configure(text='차량 ID를 등록·선택하세요. 이름에서 long/short를 추측하지 않습니다.');return
        try:
            from .mileage import number
            km=number(self.vars['distance'].get(),'거리')
            amount=format(Decimal(km)*Decimal(row['rate']),',f')+'원 (예상)'
        except ValueError: amount='거리를 입력하세요'
        self.estimate.configure(text=f"{row['kind']} · {row['rate']}원/km · {amount}\n실제 Concur 환급률·반올림·저장 금액은 별도 확인이 필요합니다.")

    def pick_image(self):
        path=filedialog.askopenfilename(parent=self,title='지도 이미지 선택',filetypes=[('지도 이미지','*.png *.jpg *.jpeg')])
        if path: self.chosen_image=path;self.map_label.configure(text=Path(path).name)

    def apply(self):
        try:
            selected=self.selected_vehicle()
            if not selected: raise ValueError('차량 관리에서 본인의 Concur 차량 ID를 등록·선택하세요.')
            value={**self.original,**{k:v.get() for k,v in self.vars.items()},**selected,
                   'description':self.description.get('1.0','end-1c'),**self.map_fields}
            # Validate fields before copying the image. Revalidate its real path below.
            candidate=checked_row({**value,'map':value.get('map') or ('pending' if self.chosen_image else ''),
                                  'map_sha256':value.get('map_sha256') or ('pending' if self.chosen_image else '')})
            if self.panel.book.rows!=self.snapshot: raise ValueError('마일리지 목록이 변경되었습니다. 닫고 다시 열어 주세요.')
            if self.chosen_image: candidate.update(self.panel.book.attach_image(self.chosen_image))
            self.panel.book.check_image(candidate)
            rows=deepcopy(self.snapshot)
            if self.index is None: rows.append(candidate)
            else: rows[self.index]=candidate
            self.panel.book.commit_rows(rows);self.panel.render()
        except (ValueError,OSError) as exc:
            self.error.configure(text=str(exc));return False
        super().close();return True


class MileagePanel(ttk.LabelFrame):
    COLUMNS=[('date','거래 날짜',105),('origin','출발지',150),('destination','도착지',150),('vehicle','차량 ID',160),
             ('kind','구분',65),('distance','거리(km)',85),('rate','환급률',80),('estimate','예상금액(원)',110),
             ('passengers','탑승자 수',90),('description','설명',220),('map_name','지도 이미지',150)]

    def __init__(self,parent,folder,on_concur=None):
        self.book=MileageBook(folder)
        self.on_concur=on_concur
        super().__init__(parent,text='차량 마일리지 · 카드 경비와 별도 관리',padding=6)
        self.columnconfigure(0,weight=1);self.rowconfigure(2,weight=1)
        ttk.Label(self,text='카드 경비와 별도 관리 · [Concur 신규 생성]은 저장된 마일리지만 새 경비로 만듭니다.',wraplength=800).grid(row=0,column=0,sticky='w')
        actions=ttk.Frame(self);actions.grid(row=1,column=0,sticky='ew',pady=4)
        for text,cmd in [('추가',self.add),('선택 수정',self.edit),('선택 삭제',self.remove),('지도 보기',self.preview),
                         ('되돌리기',self.undo),('다시 실행',self.redo),('마일리지 저장',self.save),('Concur 신규 생성',self.send_concur)]:
            ttk.Button(actions,text=text,command=cmd).pack(side='left',padx=2)
        area=ttk.Frame(self);area.grid(row=2,column=0,sticky='nsew');area.columnconfigure(0,weight=1);area.rowconfigure(0,weight=1)
        self.table=ttk.Treeview(area,columns=[k for k,_,_ in self.COLUMNS],show='headings',height=5,selectmode='browse')
        for key,label,width in self.COLUMNS: self.table.heading(key,text=label);self.table.column(key,width=width,minwidth=50,stretch=False)
        self.table.grid(row=0,column=0,sticky='nsew')
        for orient,row,col in [('vertical',0,1),('horizontal',1,0)]:
            bar=ttk.Scrollbar(area,orient=orient,command=self.table.yview if orient=='vertical' else self.table.xview)
            bar.grid(row=row,column=col,sticky='ns' if orient=='vertical' else 'ew')
            self.table.configure(**{'yscrollcommand' if orient=='vertical' else 'xscrollcommand':bar.set})
        self.table.bind('<Double-1>',lambda event:self.edit());self.table.bind('<Return>',lambda event:self.edit())
        self.table.bind('<Control-z>',lambda event:(self.undo(),'break')[1]);self.table.bind('<Control-y>',lambda event:(self.redo(),'break')[1])
        self.note=ttk.Label(self,wraplength=800);self.note.grid(row=3,column=0,sticky='w',pady=4)
        self.render()

    def render(self):
        selected=self.table.selection()
        self.table.delete(*self.table.get_children())
        for row in self.book.rows:
            self.table.insert('','end',iid=row['id'],values=[row.get(k,'') for k,_,_ in self.COLUMNS])
        if selected and self.table.exists(selected[0]): self.table.selection_set(selected[0])
        verified=sum(r.get('concur_state')=='verified' for r in self.book.rows)
        review=sum(r.get('concur_state')=='needs_review' for r in self.book.rows)
        self.note.configure(text=f"{len(self.book.rows)}건 · "+('저장 전 변경 있음' if self.book.dirty else '로컬 저장 상태')+f' · Concur 확인 {verified}건 · 확인 필요 {review}건')

    def index(self):
        selected=self.table.selection()
        if not selected: return None
        return next((i for i,r in enumerate(self.book.rows) if r['id']==selected[0]),None)

    def add(self):
        try: return MileageForm(self)
        except (OSError,ValueError) as exc: messagebox.showerror('마일리지 입력',str(exc),parent=self)

    def edit(self):
        index=self.index()
        if index is None: messagebox.showinfo('행 선택','마일리지 행을 먼저 선택하세요.',parent=self);return
        try: return MileageForm(self,index)
        except (OSError,ValueError) as exc: messagebox.showerror('마일리지 입력',str(exc),parent=self)

    def remove(self):
        index=self.index()
        if index is None: return
        if not messagebox.askyesno('마일리지 행 삭제','로컬 표에서만 삭제합니다. Concur와 지도 파일은 삭제하지 않습니다. 계속할까요?',parent=self): return
        rows=deepcopy(self.book.rows);del rows[index];self.book.commit_rows(rows);self.render()

    def undo(self): self.book.undo();self.render()
    def redo(self): self.book.redo();self.render()

    def save(self):
        try: self.book.save()
        except (ValueError,OSError) as exc:
            messagebox.showerror('마일리지 저장',str(exc),parent=self);return False
        self.render();return True

    def send_concur(self):
        if not self.book.rows:
            messagebox.showinfo('마일리지 없음','Concur에 생성할 마일리지 행이 없습니다.',parent=self)
            return
        if not self.save():
            return
        if self.on_concur is None:
            messagebox.showerror('실행 연결 없음','이 창에서는 Concur 신규 생성을 시작할 수 없습니다.',parent=self)
            return
        pending=sum(r.get('concur_state') not in ('verified','needs_review') for r in self.book.rows)
        review=sum(r.get('concur_state')=='needs_review' for r in self.book.rows)
        if not pending:
            messagebox.showinfo('신규 생성 대상 없음',
                ('확인 필요한 행은 자동 재시도하지 않습니다.' if review else '모든 마일리지 행이 이미 확인되었습니다.'), parent=self)
            return
        text=f'저장된 마일리지 {pending}건을 Concur에 새 경비로 생성합니다.\n\n'
        text+='카드 경비 C단계와 별도 실행이며, 저장 결과가 불명확한 행은 자동 재시도하지 않습니다.'
        if review:
            text+=f'\n기존 확인 필요 {review}건은 제외합니다.'
        if messagebox.askokcancel('마일리지 Concur 신규 생성',text,parent=self):
            self.on_concur()
    def confirm_close(self):
        if not self.book.dirty: return True
        answer=messagebox.askyesnocancel('마일리지 저장','마일리지 표의 변경도 저장할까요? 카드 경비와 별도 파일입니다.',parent=self)
        return False if answer is None else self.save() if answer else True

    def preview(self):
        index=self.index()
        if index is None: return
        try:
            from PIL import Image,ImageTk
            path=self.book.check_image(self.book.rows[index])
            with Image.open(path) as image:
                image=image.copy();image.thumbnail((700,500))
            dialog=Dialog(self,'지도 이미지 확인')
            picture=ImageTk.PhotoImage(image,master=dialog)
            label=ttk.Label(dialog,image=picture);label.image=picture;label.pack(fill='both',expand=True)
            dialog.present()
        except (ValueError,OSError) as exc: messagebox.showerror('지도 이미지',str(exc),parent=self)
