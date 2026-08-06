"""브라우저 페이지 구조를 덤프한다. 셀렉터를 추측하지 않기 위한 도구다.

현대카드와 Concur 화면은 여기서 볼 수 없으므로, 실제 셀렉터는 이 도구로
페이지를 떠본 뒤에 쓴다. 로그인은 사람이 직접 한다. 간편인증/OTP/SSO는
자동화 대상이 아니고, 프로필을 남겨두면 다음 실행부터 로그인이 유지된다.

    python -m src.inspect_page https://mycompany.hyundaicard.com/hs/cs/HSCS1002.do
    python -m src.inspect_page https://eu2.concursolutions.com/home --name concur

브라우저가 뜨면 직접 로그인하고 원하는 화면(예: 7월 이용내역)까지 이동한 뒤,
터미널로 돌아와 Enter를 눌러라. 그 시점의 화면이 inspect-out/ 에 저장된다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import console

PROFILE_DIR = Path("browser-profile")
OUT_DIR = Path("inspect-out")

# 화면에서 눌러야 할 만한 것들만 추린다. 전체 DOM은 따로 저장한다.
INTERACTIVE_JS = """
() => {
  const sel = 'a, button, input, select, [role=button], [onclick]';
  return [...document.querySelectorAll(sel)].map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    text: (el.innerText || el.value || '').trim().slice(0, 60),
    id: el.id || null,
    name: el.getAttribute('name'),
    cls: el.className && typeof el.className === 'string'
         ? el.className.slice(0, 80) : null,
    href: el.getAttribute('href'),
    onclick: (el.getAttribute('onclick') || '').slice(0, 120) || null,
  })).filter(e => e.text || e.id || e.name || e.onclick);
}
"""


def _snapshot(pages, out: Path) -> None:
    """열려 있는 창을 전부 저장한다.

    전표 인쇄처럼 팝업을 띄우는 화면이 있어서 첫 창만 봐서는 안 된다.
    """
    out.mkdir(parents=True, exist_ok=True)
    captured = []
    for pi, page in enumerate(pages):
        tag = f"page{pi}"
        try:
            page.screenshot(path=str(out / f"{tag}.png"), full_page=True)
        except Exception:
            pass  # 닫히는 중인 팝업은 캡처가 실패할 수 있다
        frames = []
        for fi, frame in enumerate(page.frames):
            label = f"{tag}_frame{fi}"
            try:
                html = frame.content()
                (out / f"{label}.html").write_text(html, encoding="utf-8")
                elements = frame.evaluate(INTERACTIVE_JS)
            except Exception as exc:  # 크로스오리진 프레임은 못 읽는다
                html, elements = "", [{"error": str(exc)}]
            frames.append(
                {
                    "label": label,
                    "url": frame.url,
                    "name": frame.name,
                    "html_bytes": len(html),
                    "elements": elements,
                }
            )
        captured.append({"tag": tag, "url": page.url, "frames": frames})

    (out / "elements.json").write_text(
        json.dumps({"pages": captured}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    every = [e for p in captured for f in p["frames"] for e in f["elements"]]
    logged_in = any("로그아웃" in (e.get("text") or "") for e in every)
    print(
        f"  저장: {out}  창 {len(captured)}개, 요소 {len(every)}개, "
        f"로그인 {'됨' if logged_in else '안 됨 -- 확인할 것'}"
    )


def dump(url: str, name: str, channel: str | None) -> None:
    out = OUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # 회사 SSO에 조건부 액세스가 걸려 있으면 번들 Chromium이 '관리되지 않는
        # 브라우저'로 막힐 수 있다. 그때는 --channel chrome 으로 설치된 Chrome을 쓴다.
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR / name),
            headless=False,
            accept_downloads=True,
            locale="ko-KR",
            **({"channel": channel} if channel else {}),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print("\n브라우저가 열렸다. 로그인하고 원하는 화면까지 이동해라.")
        print("화면마다 Enter를 누르면 그 시점이 저장된다. 끝내려면 q + Enter.\n")

        n = 0
        while input(f"[{n + 1}] Enter=캡처, q=종료: ").strip().lower() != "q":
            n += 1
            _snapshot(ctx.pages, out / f"{n:02d}")

        ctx.close()
        print(f"\n{n}개 화면을 {out}/ 에 저장했다.")


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="페이지 구조 덤프 (셀렉터 탐색용)")
    ap.add_argument("url")
    ap.add_argument("--name", default="page", help="저장 폴더 이름 (예: hyundaicard, concur)")
    ap.add_argument(
        "--channel",
        choices=("chrome", "msedge"),
        help="SSO가 번들 Chromium을 막을 때 설치된 브라우저를 쓴다",
    )
    args = ap.parse_args()
    dump(args.url, args.name, args.channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
