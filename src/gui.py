"""설정 편집 + 단계 실행 창.

    python -m src.gui

Tkinter를 쓴다. Windows 파이썬에 기본으로 들어 있어서 따로 설치할 게 없다.

단계는 이 창 안에서 돈다. 예전에는 새 콘솔 창을 띄웠는데, 카드 인증이나 로그인을
마치고 'Enter' 를 누르라는 안내가 그 검은 창에 떠서 어디를 봐야 하는지 알기
어려웠다. 지금은 진행 상황이 아래 칸에 찍히고, 눌러야 할 때는 창이 뜬다.

브라우저 작업이 오래 걸리므로 별도 스레드에서 돌린다. tkinter 위젯은 메인
스레드에서만 건드려야 해서, 스레드는 큐에만 넣고 화면 갱신은 after()가 한다.
"""

from __future__ import annotations

import importlib
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import console, paths, retry, settings
from .concur_formats import show_settings as show_concur_formats
from .calendar_input import DateEntry, RangePicker, initial_period, checked_period
from .calendar_widgets import date_text, period_preset
from .date_input import parse_date


# 버튼이 부르는 단계들. 창이 뜨자마자 미리 불러둔다.
STEP_MODULES = ("download_slips", "organize", "update_concur", "fix_expenses", "mileage_concur")


def _module(name: str, quiet: bool = False):
    """단계 모듈을 불러온다. 파일이 잠겨 있으면 잠깐 뒤에 다시 해본다.

    작업 스레드에서 부른다 - 기다리는 동안 창이 멈추면 안 된다.
    """
    return retry.keep_trying(
        f"src/{name}.py",
        lambda: importlib.import_module(f".{name}", __package__),
        quiet=quiet,
    )


def _preload() -> None:
    """창이 뜨자마자 단계 모듈을 뒤에서 미리 불러둔다.

    실측(회사 PC): 창을 띄우고 곧바로 버튼을 누르면 download_slips.py 가
    잠겨서 실패했다. 그런데 같은 임포트를 cmd에서 하면 그냥 됐고, 파일을
    직접 읽어도 됐다. 프로그램이 막 떴을 때 보안 프로그램이 그 프로세스의
    파일 접근을 훑는 시간과 겹친 것이다.

    미리 읽어두면 그 시간이 사람이 창을 보는 시간과 겹친다. 한 번 올라오면
    메모리에 남으므로 버튼을 누를 때는 파일을 보지 않는다.

    실패해도 조용히 넘어간다. 사람이 시킨 일이 아니고, 버튼을 누를 때 제대로
    다시 해본다 - 그때는 이유도 화면에 적힌다.
    """

    def work() -> None:
        for name in STEP_MODULES:
            try:
                _module(name, quiet=True)
            except Exception:
                pass  # 버튼을 누를 때 다시 해본다

    threading.Thread(target=work, daemon=True).start()


class _Writer:
    """print() 출력을 큐로 보낸다. 화면에 붙이는 것은 메인 스레드가 한다."""

    def __init__(self, box: queue.Queue) -> None:
        self.box = box

    def write(self, text: str) -> int:
        if text:
            self.box.put(("log", text))
        return len(text)

    def flush(self) -> None:
        pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Auto-Concur · 코드 {paths.stamp()}")
        self.cfg = settings.load()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self._build()
        self.after(100, self._drain)
        _preload()

    # --- 화면 ---------------------------------------------------------------

    def _build(self) -> None:
        from .workspace_ui import build_workspace
        build_workspace(self)

    def show_results(self):
        self.result_tab.reload()
        self.tabs.select(self.result_tab)

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log.get('1.0', 'end-1c'))

    def close_window(self):
        if self.busy:
            messagebox.showinfo('작업 진행 중', '반영 중 강제 종료하면 저장 결과를 확정할 수 없습니다. 현재 작업이 끝난 뒤 닫아 주세요.', parent=self)
            return
        self.destroy()

    def set_period(self, name):
        first, last = period_preset(name)
        self.from_date.set(date_text(first, '.'))
        self.to_date.set(date_text(last, '.'))

    def pick_period(self):
        def apply(first, last):
            self.from_date.set(first.replace('-', '.'))
            self.to_date.set(last.replace('-', '.'))
        RangePicker(self, parse_date(self.from_date.get()), parse_date(self.from_date.get()),
                    parse_date(self.to_date.get()), apply)

    def pick_folder(self) -> None:
        # 프로그램이 있는 폴더에서 시작한다. 적혀 있는 폴더가 실제로 있으면
        # 거기서 시작한다 - 없는 경로를 주면 창이 엉뚱한 데서 열린다.
        here = Path(self.folder.get().strip() or paths.base())
        if not here.is_dir():
            here = paths.base()
        chosen = filedialog.askdirectory(title="전표 폴더 선택", initialdir=str(here))
        if chosen:
            self.folder.set(chosen)

    def edit_worksheet(self) -> None:
        from .worksheet_editor import Editor
        from .update_concur import pick_sheet
        folder = paths.folder(self.folder.get().strip())
        source = pick_sheet(folder, None)
        if source is None:
            messagebox.showinfo('작업지 없음', '먼저 B. 파싱 · 작업지 생성을 실행해 주세요.', parent=self)
            return
        try:
            cfg = {**self.cfg, 'attendee_default': self.attendee.get().strip()}
            Editor(self, source, cfg, on_run=self.step_update, on_mileage=self.step_mileage)
        except Exception as exc:
            messagebox.showerror('작업지를 열지 못했습니다', str(exc), parent=self)

    def _say(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    # --- 단계 실행 -----------------------------------------------------------

    def _drain(self) -> None:
        """스레드가 큐에 넣은 것을 화면에 옮긴다. 메인 스레드에서만 돈다."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "ask":
                    message, done = payload
                    self.run_state.set("사용자 확인 대기 · 안내창을 확인하세요.")
                    messagebox.showinfo("확인", message, parent=self)
                    self.run_state.set("작업을 계속 진행합니다.")
                    done.set()
                elif kind == 'confirm_action':
                    message, action, done, answer = payload
                    self.run_state.set('리포트 확인 대기 · 대상과 건수를 확인한 뒤 시작하세요.')
                    dialog = tk.Toplevel(self)
                    dialog.title('Concur 작업 대상 확인')
                    dialog.transient(self)
                    frame = ttk.Frame(dialog, padding=18)
                    frame.pack(fill='both', expand=True)
                    ttk.Label(frame, text=message, wraplength=580).pack(pady=(0, 16))
                    def finish(value, window=dialog, result=answer, event=done):
                        self.run_state.set('작업을 계속 진행합니다.' if value else '반영 취소를 처리합니다.')
                        result.append(value)
                        window.destroy()
                        event.set()
                    ttk.Button(frame, text=action, command=lambda f=finish: f(True)).pack(side='left', padx=6)
                    ttk.Button(frame, text='취소', command=lambda f=finish: f(False)).pack(side='left', padx=6)
                    dialog.protocol('WM_DELETE_WINDOW', lambda f=finish: f(False))
                    dialog.grab_set()
                elif kind == "note":
                    self._say(payload, "강조")
                elif kind == "end":
                    self.busy = False
                    for button in self.buttons:
                        button.state(["!disabled"])
                    self._say(payload)
                    from .workspace_ui import set_busy
                    set_busy(self, False, payload.strip())
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _ask(self, message: str) -> None:
        """단계 스레드가 부른다. 창이 뜨고 사람이 누를 때까지 여기서 기다린다."""
        done = threading.Event()
        self.events.put(("ask", (message.strip(), done)))
        done.wait()

    def _confirm_action(self, message, action):
        done, answer = threading.Event(), []
        self.events.put(('confirm_action', (message, action, done, answer)))
        done.wait()
        return bool(answer and answer[0])

    def _start(self, title: str, work, note: str = "") -> None:
        if self.busy:
            messagebox.showinfo("잠깐만요", "앞 단계가 아직 돌고 있습니다.", parent=self)
            return
        # 설정을 못 저장해도 단계는 돌아야 한다. 창에 적은 값은 이미 메모리에
        # 있고, settings.json은 다음 실행 때 편하려고 남기는 것뿐이다.
        # (실측: 회사 PC에서 settings.json 이 잠겨 PermissionError로 단계가
        #  시작도 못 했다.)
        text = self.limit.get().strip()
        if text and (not text.isascii() or not text.isdecimal() or int(text) < 1):
            messagebox.showerror('처리 건수 확인', '앞 N건에는 1 이상의 정수를 입력하세요. 전체 처리는 빈칸으로 두세요.', parent=self)
            return
        try:
            self.save()
        except OSError as exc:
            self._say(f"\n(설정을 저장하지 못했습니다: {exc}\n 이번 실행에는 창의 값을 씁니다.)\n")
        self.busy = True
        from .workspace_ui import set_busy
        set_busy(self, True, title + " · 진행 중 (실제 완료율은 아직 확인되지 않았습니다)")
        for button in self.buttons:
            button.state(["disabled"])
        self._say(f"\n{'=' * 60}\n{title}\n{'=' * 60}\n")

        def run() -> None:
            import sys

            writer = _Writer(self.events)
            sys.stdout = sys.stderr = writer
            console.set_prompt(self._ask)
            console.set_confirmation(self._confirm_action)
            try:
                result = work()
                suffix = getattr(result, 'summary', None) or ('일부 작업이 실패했습니다. 위 오류를 확인해 주세요.' if result else '마쳤습니다.')
                self.events.put(("end", f"\n{title}: {suffix}\n"))
                if note and not result:
                    self.events.put(("note", f"\n{note}\n"))
            except Exception as exc:
                writer.write("\n" + traceback.format_exc())
                self.events.put(("end", f"\n{title} 중에 멈췄습니다: {exc}\n"))
            finally:
                console.set_prompt(None)
                console.set_confirmation(None)
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

        threading.Thread(target=run, daemon=True).start()

    def save(self) -> None:
        self.cfg["downloads_dir"] = self.folder.get().strip()  # 빈 값은 프로그램 폴더
        self.cfg["attendee_default"] = self.attendee.get().strip()
        self.cfg["period_from"] = self.from_date.get()
        self.cfg["period_to"] = self.to_date.get()
        settings.save(self.cfg)

    def _limit(self) -> int | None:
        text = self.limit.get().strip()
        return int(text) if text.isdigit() else None

    def step_download(self) -> None:
        try:
            from_date, to_date = checked_period(self.from_date.get(), self.to_date.get())
        except ValueError as exc:
            messagebox.showerror("조회 기간 확인", str(exc), parent=self)
            return
        limit = self._limit()
        def work() -> None:
            download_slips = _module("download_slips")
            return download_slips.download(
                download_slips._norm_date(from_date),
                download_slips._norm_date(to_date),
                paths.folder(self.cfg["downloads_dir"]),
                limit,
            )

        self._start(
            "A. 전표 다운로드",
            work,
            "다음: [B. 파싱 · 작업지 생성] 을 눌러 주세요.",
        )

    def step_organize(self) -> None:
        def work() -> None:
            return _module("organize").organize(paths.folder(self.cfg["downloads_dir"]), True)

        self._start(
            "B. 파싱 · 작업지 생성",
            work,
            "다음: [작업지 편집] 에서 입력해 주세요.\n"
            "      빈칸은 유지하며 영수증이 없는 경비에는 영수증만 첨부합니다.\n"
            "      [저장하고 Concur 반영] 을 누르면 C단계로 이어집니다.",
        )

    def step_update(self) -> None:
        """첨부와 입력을 한 세션에서 한다. 로그인을 두 번 하지 않아도 된다."""
        limit = self._limit()
        def work() -> None:
            update_concur = _module("update_concur")
            folder = paths.folder(self.cfg["downloads_dir"])
            return update_concur.run(
                folder,
                True,
                int(self.cfg["date_tolerance_days"]),
                limit,
                update_concur.pick_sheet(folder, None),
                cfg=dict(self.cfg),
            )

        self._start(
            "C. Concur 반영",
            work,
            "Concur 화면에서 결과를 확인해 주세요. 실패한 건이 있으면 위 기록에 남아 있습니다.",
        )

    def step_mileage(self) -> None:
        limit = self._limit()
        def work() -> None:
            mileage_concur = _module('mileage_concur')
            return mileage_concur.run(paths.folder(self.cfg['downloads_dir']), True, limit)

        self._start(
            '마일리지 Concur 신규 생성',
            work,
            'Concur 화면에서 새 마일리지 경비를 확인해 주세요. 확인 필요 행은 자동 재시도하지 않습니다.',
        )
    def step_list_types(self) -> None:
        self._start(
            "경비유형 코드 확인", lambda: _module("fix_expenses").run(False, None, True)
        )

    def step_list_lodging(self) -> None:
        self._start(
            "숙박비 목록 확인",
            lambda: _module("fix_expenses").run(False, None, False, None, True),
        )


def main() -> int:
    console.setup()
    # Tk 창 생성 전에 설정하여 Windows의 비트맵 확대에 따른 흐림을 줄인다.
    import sys
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
