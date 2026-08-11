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

from . import browser, console, paths

PROFILE_DIR = paths.at("browser-profile")
OUT_DIR = paths.at("inspect-out")

# 화면에서 눌러야 할 만한 것들만 추린다. 전체 DOM은 따로 저장한다.
# combobox/listbox까지 봐야 한다. Concur의 경비 유형 드롭다운은 select가 아니라
# div[role=combobox]라서, a/button/input/select만 훑으면 아예 안 잡힌다.
INTERACTIVE_JS = """
() => {
  const sel = 'a, button, input, select, textarea, [role=button], [role=combobox],'
            + ' [role=listbox], [role=option], [aria-haspopup], [onclick]';
  return [...document.querySelectorAll(sel)].map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type'),
    text: (el.innerText || el.value || '').trim().slice(0, 60),
    id: el.id || null,
    name: el.getAttribute('name'),
    cls: el.className && typeof el.className === 'string'
         ? el.className.slice(0, 80) : null,
    role: el.getAttribute('role'),
    href: el.getAttribute('href'),
    onclick: (el.getAttribute('onclick') || '').slice(0, 120) || null,
  })).filter(e => e.text || e.id || e.name || e.onclick || e.role);
}
"""

# 입력 필드를 라벨과 함께 뽑는다. id가 :r1ub: 처럼 React가 만든 것이면 이름만
# 봐서는 무슨 필드인지 알 수 없다. '경비 유형' 라벨이 붙은 게 뭔지 알아야 한다.
FORM_FIELDS_JS = """
() => {
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\\s+/)
        .map(id => (document.getElementById(id) || {}).innerText || '')
        .join(' ').trim();
      if (t) return t;
    }
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return (l.innerText || '').trim();
    }
    const own = el.closest('label');
    if (own) return (own.innerText || '').trim();
    const wrap = el.closest('[class*="form-field"], [class*="form-group"]');
    if (wrap) {
      const l = wrap.querySelector('label');
      if (l) return (l.innerText || '').trim();
    }
    return '';
  };
  const sel = 'input, select, textarea, [role=combobox], [contenteditable="true"]';
  return [...document.querySelectorAll(sel)]
    .filter(el => el.type !== 'hidden')
    .map(el => ({
      label: labelOf(el).slice(0, 60),
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type'),
      role: el.getAttribute('role'),
      id: el.id || null,
      name: el.getAttribute('name'),
      value: (el.value !== undefined ? el.value : el.innerText || '').slice(0, 60),
      visible: el.offsetParent !== null,
    }));
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
                fields = frame.evaluate(FORM_FIELDS_JS)
            except Exception as exc:  # 크로스오리진 프레임은 못 읽는다
                html, elements, fields = "", [{"error": str(exc)}], []
            frames.append(
                {
                    "label": label,
                    "url": frame.url,
                    "name": frame.name,
                    "html_bytes": len(html),
                    "elements": elements,
                    "fields": fields,
                }
            )
        captured.append({"tag": tag, "url": page.url, "frames": frames})

    (out / "elements.json").write_text(
        json.dumps({"pages": captured}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 입력 필드는 따로 뽑아둔다. 셀렉터를 찾을 때 제일 먼저 보는 파일이다.
    fields = [
        f for p in captured for fr in p["frames"] for f in fr["fields"] if f.get("visible")
    ]
    (out / "fields.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    every = [e for p in captured for f in p["frames"] for e in f["elements"]]
    logged_in = any("로그아웃" in (e.get("text") or "") for e in every)
    print(
        f"  저장: {out}  창 {len(captured)}개, 요소 {len(every)}개, "
        f"입력 필드 {len(fields)}개, "
        f"로그인 {'됨' if logged_in else '안 됨 -- 확인해 주세요'}"
    )


def dump(url: str, name: str, channel: str | None) -> None:
    out = OUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # 회사 SSO에 조건부 액세스가 걸려 있으면 번들 Chromium이 '관리되지 않는
        # 브라우저'로 막힐 수 있다. 그때는 --channel chrome 으로 설치된 Chrome을 쓴다.
        ctx = browser.launch(
            p, PROFILE_DIR / name, only=channel, accept_downloads=True, locale="ko-KR"
        )
        page = browser.open_first(ctx, url)

        print("\n브라우저가 열렸습니다. 로그인하시고 원하는 화면까지 이동해 주세요.")
        print("화면마다 Enter를 누르시면 그 시점이 저장됩니다. 끝내시려면 q + Enter.\n")

        n = 0
        while input(f"[{n + 1}] Enter=캡처, q=종료: ").strip().lower() != "q":
            n += 1
            _snapshot(ctx.pages, out / f"{n:02d}")

        ctx.close()
        print(f"\n{n}개 화면을 {out}/ 에 저장했습니다.")


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="페이지 구조 덤프 (셀렉터 탐색용)")
    ap.add_argument("url")
    ap.add_argument("--name", default="page", help="저장 폴더 이름입니다 (예: hyundaicard, concur)")
    ap.add_argument(
        "--channel",
        choices=("chrome", "msedge"),
        help="SSO가 번들 Chromium을 막을 때 설치된 브라우저를 씁니다",
    )
    args = ap.parse_args()
    dump(args.url, args.name, args.channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
