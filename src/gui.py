"""설정 편집 + 단계 실행 창.

    python -m src.gui

Tkinter를 쓴다. Windows 파이썬에 기본으로 들어 있어서 따로 설치할 게 없다.

각 단계는 새 콘솔 창에서 돌린다. 카드 인증이나 로그인을 마치고 Enter를 눌러야
하는 대기가 있어서, 창 안에 출력을 가두면 그 조작을 할 수 없다.

창에는 기간·전표 폴더·참석자만 둔다. 나머지는 전부 작업지(엑셀)에서 정한다.
참석자는 늘 같은 사람이라 여기서 한 번 적어두면 작업지의 '내부 직원간 식음료'
행마다 자동으로 들어간다. 다른 사람이면 엑셀에서 고치면 된다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, ttk

from . import console, settings


def _run(args: list[str]) -> None:
    """새 콘솔 창에서 단계를 돌린다.

    끝나도 창이 저절로 닫히지 않게 한다. 닫혀버리면 마지막에 찍힌 안내나
    오류를 읽을 수 없다.
    """
    cmd = [sys.executable, "-m", *args]
    env = {**os.environ, console.HOLD_ENV: "1"}
    kwargs = {"env": env}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, **kwargs)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Concur 경비 자동화")
        self.resizable(False, False)
        self.cfg = settings.load()
        self._build()

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

        self.apply = tk.BooleanVar(value=False)
        self.limit = tk.StringVar(value="")
        opts = ttk.Frame(run)
        opts.grid(row=0, column=0, sticky="w", **pad)
        ttk.Checkbutton(
            opts, text="실제로 반영합니다 (체크하지 않으면 계획만 보여 드립니다)", variable=self.apply
        ).pack(side="left")
        ttk.Label(opts, text="   앞에서 N건만:").pack(side="left")
        ttk.Entry(opts, textvariable=self.limit, width=6).pack(side="left")

        steps = [
            ("A. 전표 다운로드", self.step_download),
            ("B. 파싱 · 작업지 생성", self.step_organize),
            ("C. Concur 반영", self.step_update),
        ]
        bar = ttk.Frame(run)
        bar.grid(row=1, column=0, sticky="w", **pad)
        for text, cmd in steps:
            ttk.Button(bar, text=text, width=22, command=cmd).pack(side="left", padx=3)

        tools = ttk.Frame(self)
        tools.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 10))
        ttk.Button(tools, text="경비유형 코드 확인", command=self.step_list_types).pack(side="left")
        ttk.Button(tools, text="숙박비 목록 확인", command=self.step_list_lodging).pack(
            side="left", padx=6
        )
        ttk.Label(
            tools, text="드롭다운 값이 바뀌었을 때 다시 뽑습니다", foreground="#666"
        ).pack(side="left", padx=8)

    def pick_folder(self) -> None:
        chosen = filedialog.askdirectory(
            title="전표 폴더 선택", initialdir=self.folder.get() or "."
        )
        if chosen:
            self.folder.set(chosen)

    def save(self) -> bool:
        self.cfg["downloads_dir"] = self.folder.get().strip() or "downloads"
        self.cfg["attendee_default"] = self.attendee.get().strip()
        settings.save(self.cfg)
        return True

    def _common(self) -> list[str]:
        args = []
        if self.apply.get():
            args.append("--apply")
        if self.limit.get().strip():
            args += ["--limit", self.limit.get().strip()]
        return args

    def step_download(self) -> None:
        """설정을 먼저 저장한다. 단계들이 settings.json 을 읽기 때문이다."""
        self.save()
        args = ["src.download_slips", "--from", self.from_date.get(), "--to", self.to_date.get(),
                "--out", self.cfg["downloads_dir"]]
        if self.limit.get().strip():
            args += ["--limit", self.limit.get().strip()]
        _run(args)

    def step_organize(self) -> None:
        self.save()
        args = ["src.organize", self.cfg["downloads_dir"]]
        if self.apply.get():
            args.append("--apply")
        _run(args)

    def step_update(self) -> None:
        """첨부와 입력을 한 세션에서 한다. 로그인을 두 번 하지 않아도 된다."""
        self.save()
        _run(["src.update_concur", "--dir", self.cfg["downloads_dir"], *self._common()])

    def step_list_types(self) -> None:
        self.save()
        _run(["src.fix_expenses", "--list-types"])

    def step_list_lodging(self) -> None:
        self.save()
        _run(["src.fix_expenses", "--list-lodging"])


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
