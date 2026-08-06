"""브라우저 페이지 구조를 덤프한다. 셀렉터를 추측하지 않기 위한 도구다.

현대카드와 Concur 화면은 여기서 볼 수 없으므로, 실제 셀렉터는 이 도구로
페이지를 떠본 뒤에 쓴다. 로그인은 사람이 직접 한다 — 간편인증/OTP/SSO는
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


def dump(url: str, name: str) -> None:
    out = OUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR / name),
            headless=False,
            accept_downloads=True,
            locale="ko-KR",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print(f"\n브라우저가 열렸다. 로그인하고 원하는 화면까지 이동한 뒤")
        input("여기로 돌아와서 Enter를 눌러라... ")

        page.screenshot(path=str(out / "screen.png"), full_page=True)

        frames = []
        for i, frame in enumerate(page.frames):
            label = f"frame{i}"
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

        (out / "elements.json").write_text(
            json.dumps({"page_url": page.url, "frames": frames}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"\n저장 완료: {out}/")
        print(f"  screen.png       화면 캡처")
        print(f"  frame*.html      프레임별 HTML ({len(frames)}개)")
        print(f"  elements.json    누를 수 있는 요소 목록")
        for f in frames:
            n = len(f["elements"])
            print(f"    - {f['label']}: {n}개 요소  {f['url'][:70]}")

        ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="페이지 구조 덤프 (셀렉터 탐색용)")
    ap.add_argument("url")
    ap.add_argument("--name", default="page", help="저장 폴더 이름 (예: hyundaicard, concur)")
    args = ap.parse_args()
    dump(args.url, args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
