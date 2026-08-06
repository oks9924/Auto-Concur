"""설정 편집 + 단계 실행 창.

    python -m src.gui

Tkinter를 쓴다. Windows 파이썬에 기본으로 들어 있어서 따로 설치할 게 없다.

각 단계는 새 콘솔 창에서 돌린다. 카드 인증이나 로그인을 마치고 Enter를 눌러야
하는 대기가 있어서, 창 안에 출력을 가두면 그 조작을 할 수 없다.
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from . import settings

# 설정 창에 띄울 항목. (설정키, 라벨, 설명)
FIELDS = [
    ("business_purpose", "비즈니스 목적", "식대 건에 채울 문구"),
    ("comment", "코멘트", "식대 건에 채울 문구"),
    ("attendee_query", "참석자 검색어", "Concur 참석자 검색에 넣을 값"),
    ("large_amount_type", "큰 금액 경비유형", "임계금액 이상이면 이 유형으로 바꾼다"),
    ("lodging_threshold", "임계 금액", "이 금액 이상 (원)"),
    ("date_tolerance_days", "날짜 허용 오차", "영수증 매칭에서 허용할 일수"),
    ("downloads_dir", "전표 폴더", "전표 PDF와 manifest.csv 위치"),
]

INT_FIELDS = {"lodging_threshold", "date_tolerance_days"}


def _run(args: list[str]) -> None:
    """새 콘솔 창에서 단계를 돌린다."""
    cmd = [sys.executable, "-m", *args]
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, **kwargs)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Concur 경비 자동화")
        self.resizable(False, False)
        self.cfg = settings.load()
        self.vars: dict[str, tk.StringVar] = {}
        self._build()

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}

        box = ttk.LabelFrame(self, text="설정")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        for i, (key, label, hint) in enumerate(FIELDS):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", **pad)
            var = tk.StringVar(value=str(self.cfg.get(key, "")))
            self.vars[key] = var
            ttk.Entry(box, textvariable=var, width=34).grid(row=i, column=1, **pad)
            ttk.Label(box, text=hint, foreground="#666").grid(row=i, column=2, sticky="w", **pad)
        ttk.Button(box, text="설정 저장", command=self.save).grid(
            row=len(FIELDS), column=1, sticky="e", **pad
        )

        run = ttk.LabelFrame(self, text="실행")
        run.grid(row=1, column=0, sticky="ew", padx=10, pady=4)

        ttk.Label(run, text="기간").grid(row=0, column=0, sticky="w", **pad)
        self.from_date = tk.StringVar(value="2026.08.01")
        self.to_date = tk.StringVar(value="2026.08.31")
        span = ttk.Frame(run)
        span.grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        ttk.Entry(span, textvariable=self.from_date, width=12).pack(side="left")
        ttk.Label(span, text=" ~ ").pack(side="left")
        ttk.Entry(span, textvariable=self.to_date, width=12).pack(side="left")

        self.apply = tk.BooleanVar(value=False)
        self.limit = tk.StringVar(value="")
        opts = ttk.Frame(run)
        opts.grid(row=1, column=0, columnspan=3, sticky="w", **pad)
        ttk.Checkbutton(
            opts, text="실제로 반영 (체크 안 하면 계획만 본다)", variable=self.apply
        ).pack(side="left")
        ttk.Label(opts, text="   앞에서 N건만:").pack(side="left")
        ttk.Entry(opts, textvariable=self.limit, width=6).pack(side="left")

        steps = [
            ("A. 전표 다운로드", self.step_download),
            ("B. 파싱 · 작업지 생성", self.step_organize),
            ("C. Concur 반영", self.step_update),
        ]
        bar = ttk.Frame(run)
        bar.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        for text, cmd in steps:
            ttk.Button(bar, text=text, width=22, command=cmd).pack(side="left", padx=3)

        tools = ttk.Frame(self)
        tools.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 10))
        ttk.Button(tools, text="경비유형 코드 확인", command=self.step_list_types).pack(side="left")
        ttk.Label(
            tools, text="새 유형(택시 등)이 생겼을 때 코드를 뽑는다", foreground="#666"
        ).pack(side="left", padx=8)

    def save(self) -> bool:
        for key, var in self.vars.items():
            value = var.get().strip()
            if key in INT_FIELDS:
                if not value.isdigit():
                    messagebox.showerror("설정", f"'{key}' 는 숫자여야 한다: {value!r}")
                    return False
                self.cfg[key] = int(value)
            else:
                self.cfg[key] = value
        settings.save(self.cfg)
        return True

    def _common(self) -> list[str]:
        """설정을 먼저 저장한다. 단계들이 settings.json 을 읽기 때문이다."""
        args = []
        if self.apply.get():
            args.append("--apply")
        if self.limit.get().strip():
            args += ["--limit", self.limit.get().strip()]
        return args

    def step_download(self) -> None:
        if not self.save():
            return
        args = ["src.download_slips", "--from", self.from_date.get(), "--to", self.to_date.get()]
        if self.limit.get().strip():
            args += ["--limit", self.limit.get().strip()]
        _run(args)

    def step_organize(self) -> None:
        if not self.save():
            return
        args = ["src.organize", self.cfg["downloads_dir"]]
        if self.apply.get():
            args.append("--apply")
        _run(args)

    def step_update(self) -> None:
        """첨부와 입력을 한 세션에서 한다. 로그인을 두 번 하지 않아도 된다."""
        if self.save():
            _run(["src.update_concur", "--dir", self.cfg["downloads_dir"], *self._common()])

    def step_list_types(self) -> None:
        if self.save():
            _run(["src.fix_expenses", "--list-types"])


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
