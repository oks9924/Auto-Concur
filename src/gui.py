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


def _module(name: str):
    """단계 모듈을 불러온다. 파일이 잠겨 있으면 잠깐 뒤에 다시 해본다.

    단계를 누르는 그 순간에 이 파일을 처음 읽으므로 잠김이 여기서 드러난다.
    작업 스레드에서 부른다 - 기다리는 동안 창이 멈추면 안 된다.
    """
    return retry.keep_trying(
        f"src/{name}.py", lambda: importlib.import_module(f".{name}", __package__)
    )


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
        self.title("Concur 경비 자동화")
        self.cfg = settings.load()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self._build()
        self.after(100, self._drain)

    # --- 화면 ---------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}

        box = ttk.LabelFrame(self, text="설정")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        ttk.Label(box, text="기간").grid(row=0, column=0, sticky="w", **pad)
        self.from_date = tk.StringVar(value="2026.08.01")
        self.to_date = tk.StringVar(value="2026.08.31")
        span = ttk.Frame(box)
        span.grid(row=0, column=1, sticky="w", **pad)
        ttk.Entry(span, textvariable=self.from_date, width=12).pack(side="left")
        ttk.Label(span, text=" ~ ").pack(side="left")
        ttk.Entry(span, textvariable=self.to_date, width=12).pack(side="left")

        # 참석자는 사람마다 고정이다. 여기 적어두면 작업지에 따라 들어간다.
        ttk.Label(box, text="참석자").grid(row=1, column=0, sticky="w", **pad)
        self.attendee = tk.StringVar(value=str(self.cfg.get("attendee_default", "")))
        who = ttk.Frame(box)
        who.grid(row=1, column=1, sticky="w", **pad)
        ttk.Entry(who, textvariable=self.attendee, width=40).pack(side="left")
        ttk.Label(
            who, text="여러 명은 쉼표로 (kyungsik.oh, hong.gildong)", foreground="#666"
        ).pack(side="left", padx=6)

        # 전표 폴더는 직접 고르게 한다. 경로를 손으로 치면 오타가 난다.
        ttk.Label(box, text="전표 폴더").grid(row=2, column=0, sticky="w", **pad)
        self.folder = tk.StringVar(value=str(self.cfg.get("downloads_dir", "downloads")))
        picker = ttk.Frame(box)
        picker.grid(row=2, column=1, sticky="w", **pad)
        ttk.Entry(picker, textvariable=self.folder, width=40).pack(side="left")
        ttk.Button(picker, text="찾아보기", command=self.pick_folder).pack(side="left", padx=6)

        run = ttk.LabelFrame(self, text="실행")
        run.grid(row=1, column=0, sticky="ew", padx=10, pady=4)

        # 버튼을 누르면 항상 실제로 반영한다. 창에서 계획만 보는 일이 없어서
        # 체크박스는 매번 켜는 손이 하나 더 가는 것뿐이었다.
        self.limit = tk.StringVar(value="")
        opts = ttk.Frame(run)
        opts.grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(opts, text="앞에서 N건만 (비워두면 전부):").pack(side="left")
        ttk.Entry(opts, textvariable=self.limit, width=6).pack(side="left", padx=6)

        self.buttons = []
        bar = ttk.Frame(run)
        bar.grid(row=1, column=0, sticky="w", **pad)
        for text, cmd in (
            ("A. 전표 다운로드", self.step_download),
            ("B. 파싱 · 작업지 생성", self.step_organize),
            ("C. Concur 반영", self.step_update),
        ):
            button = ttk.Button(bar, text=text, width=22, command=cmd)
            button.pack(side="left", padx=3)
            self.buttons.append(button)

        tools = ttk.Frame(self)
        tools.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 4))
        for text, cmd in (
            ("경비유형 코드 확인", self.step_list_types),
            ("숙박비 목록 확인", self.step_list_lodging),
        ):
            button = ttk.Button(tools, text=text, command=cmd)
            button.pack(side="left", padx=(0, 6))
            self.buttons.append(button)
        ttk.Label(
            tools, text="드롭다운 값이 바뀌었을 때 다시 뽑습니다", foreground="#666"
        ).pack(side="left", padx=8)

        self.log = scrolledtext.ScrolledText(self, width=100, height=20, state="disabled")
        self.log.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        # 다음에 뭘 해야 하는지는 굵게 찍는다. 진행 로그에 섞이면 지나친다.
        self.log.tag_configure("강조", font=("TkDefaultFont", 10, "bold"), foreground="#0a5")
        self.rowconfigure(3, weight=1)
        self.columnconfigure(0, weight=1)
        self._say("버튼을 누르면 여기에 진행 상황이 찍힙니다.\n")

    def pick_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="전표 폴더 선택", initialdir=self.folder.get() or "."
        )
        if chosen:
            self.folder.set(chosen)

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
                    messagebox.showinfo("확인", message, parent=self)
                    done.set()
                elif kind == "note":
                    self._say(payload, "강조")
                elif kind == "end":
                    self.busy = False
                    for button in self.buttons:
                        button.state(["!disabled"])
                    self._say(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _ask(self, message: str) -> None:
        """단계 스레드가 부른다. 창이 뜨고 사람이 누를 때까지 여기서 기다린다."""
        done = threading.Event()
        self.events.put(("ask", (message.strip(), done)))
        done.wait()

    def _start(self, title: str, work, note: str = "") -> None:
        if self.busy:
            messagebox.showinfo("잠깐만요", "앞 단계가 아직 돌고 있습니다.", parent=self)
            return
        # 설정을 못 저장해도 단계는 돌아야 한다. 창에 적은 값은 이미 메모리에
        # 있고, settings.json은 다음 실행 때 편하려고 남기는 것뿐이다.
        # (실측: 회사 PC에서 settings.json 이 잠겨 PermissionError로 단계가
        #  시작도 못 했다.)
        try:
            self.save()
        except OSError as exc:
            self._say(f"\n(설정을 저장하지 못했습니다: {exc}\n 이번 실행에는 창의 값을 씁니다.)\n")
        self.busy = True
        for button in self.buttons:
            button.state(["disabled"])
        self._say(f"\n{'=' * 60}\n{title}\n{'=' * 60}\n")

        def run() -> None:
            import sys

            writer = _Writer(self.events)
            sys.stdout = sys.stderr = writer
            console.set_prompt(self._ask)
            try:
                work()
                self.events.put(("end", f"\n{title} 을(를) 마쳤습니다.\n"))
                if note:
                    self.events.put(("note", f"\n{note}\n"))
            except Exception as exc:
                writer.write("\n" + traceback.format_exc())
                self.events.put(("end", f"\n{title} 중에 멈췄습니다: {exc}\n"))
            finally:
                console.set_prompt(None)
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

        threading.Thread(target=run, daemon=True).start()

    def save(self) -> None:
        self.cfg["downloads_dir"] = self.folder.get().strip() or "downloads"
        self.cfg["attendee_default"] = self.attendee.get().strip()
        settings.save(self.cfg)

    def _limit(self) -> int | None:
        text = self.limit.get().strip()
        return int(text) if text.isdigit() else None

    def step_download(self) -> None:
        def work() -> None:
            download_slips = _module("download_slips")
            download_slips.download(
                download_slips._norm_date(self.from_date.get()),
                download_slips._norm_date(self.to_date.get()),
                paths.folder(self.cfg["downloads_dir"]),
                self._limit(),
            )

        self._start(
            "A. 전표 다운로드",
            work,
            "다음: [B. 파싱 · 작업지 생성] 을 눌러 주세요.",
        )

    def step_organize(self) -> None:
        def work() -> None:
            _module("organize").organize(paths.folder(self.cfg["downloads_dir"]), True)

        self._start(
            "B. 파싱 · 작업지 생성",
            work,
            "다음: manifest.xlsx 를 열어 경비에 올릴 내용을 수정해 주세요.\n"
            "      경비유형을 고르면 채워야 할 칸이 초록으로 바뀝니다.\n"
            "      다 채우신 뒤 [C. Concur 반영] 을 눌러 주세요.",
        )

    def step_update(self) -> None:
        """첨부와 입력을 한 세션에서 한다. 로그인을 두 번 하지 않아도 된다."""
        def work() -> None:
            update_concur = _module("update_concur")
            folder = paths.folder(self.cfg["downloads_dir"])
            update_concur.run(
                folder,
                True,
                int(self.cfg["date_tolerance_days"]),
                self._limit(),
                update_concur.pick_sheet(folder, None),
            )

        self._start(
            "C. Concur 반영",
            work,
            "Concur 화면에서 결과를 확인해 주세요. 실패한 건이 있으면 위 기록에 남아 있습니다.",
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
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
