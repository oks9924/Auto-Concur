"""Concur 화면 대기와 클릭. 읽기/클릭 준비만 재시도하고 실제 클릭은 한 번 한다."""

from __future__ import annotations

import json
import time
from datetime import datetime

from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout

from . import paths


class ConcurUIError(Exception):
    """화면 상태를 확인하지 못했거나 동작 결과가 불명확하다."""


# 기존 화면에서 확인된 dialog/side-panel 범위 안에서만 입력 대상을 찾는다.
# 숨겨진 템플릿이나 뒤쪽 상세 화면이 먼저 검색되는 것을 막는다.
DOM_HELPERS = """
  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const surface = () => {
    const top = nodes => [...nodes].filter(visible).sort((a,b) =>
      (parseInt(getComputedStyle(a).zIndex) || 0) - (parseInt(getComputedStyle(b).zIndex) || 0)).pop();
    return top(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
      || top(document.querySelectorAll('[class*="side-panel__side"]')) || document;
  };
"""


def _transient(exc: PWError) -> bool:
    return any(text in str(exc) for text in (
        'Execution context was destroyed', 'Cannot find context with specified id'))


def diagnose(page, what: str) -> str:
    """진단 저장 실패가 원래 오류를 덮지 않도록 한다. 필드 값은 수집하지 않는다."""
    try:
        data = page.evaluate("""() => ({
          url: location.origin + location.pathname,
          buttons: [...document.querySelectorAll('button')].map(b => ({
            text: (b.innerText || '').trim().slice(0, 80),
            hook: b.getAttribute('data-nuiexp'), disabled: b.disabled,
            visible: !!(b.getBoundingClientRect().width && b.getBoundingClientRect().height)
          })),
          dialogs: [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
            .filter(d => d.getBoundingClientRect().width > 0)
            .map(d => (d.innerText || '').slice(0, 600))
        })""")
        out = paths.at('inspect-out')
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"ui-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
        target.write_text(json.dumps({'step': what, **data}, ensure_ascii=False, indent=2), encoding='utf-8')
        return f" (진단: {target})"
    except Exception:
        return ''


def wait_condition(page, script: str, what: str, arg=None, timeout: int = 30000):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            result = page.evaluate(script, arg)
            if result:
                return result
        except PWError as exc:
            if not _transient(exc):
                raise ConcurUIError(f"{what} 확인 중 오류: {exc}" + diagnose(page, what)) from exc
        page.wait_for_timeout(min(200, max(1, int((deadline - time.monotonic()) * 1000))))
    raise ConcurUIError(f"{what}을(를) {timeout / 1000:g}초 동안 확인하지 못했습니다" + diagnose(page, what))


def click_target(page, script: str, what: str, arg=None, timeout: int = 15000):
    """DOM 재렌더링 시 대상을 재탐색한다. 클릭 전 trial에는 부작용이 없다.

    실제 클릭에서 timeout이 나면 전송 여부를 확신할 수 없으므로 재클릭하지 않는다.
    저장/추가가 중복 수행되는 것을 막는다.
    """
    deadline = time.monotonic() + timeout / 1000
    last = '대상 없음'
    while time.monotonic() < deadline:
        target = None
        try:
            selected = page.evaluate(script, arg)
            selectors = selected if isinstance(selected, list) else [selected] if selected else []
            for selector in selectors:
                left = int((deadline - time.monotonic()) * 1000)
                if left <= 0:
                    break
                candidate = page.locator(selector)
                try:
                    candidate.click(trial=True, timeout=min(700, left))
                except PWTimeout as exc:
                    last = str(exc).splitlines()[0]
                    continue
                # 실제 동작은 try/retry 블록 밖에서 실행한다.
                target = candidate
                break
        except PWError as exc:
            if not _transient(exc):
                raise ConcurUIError(f"{what} 준비 중 오류: {exc}" + diagnose(page, what)) from exc
            target = None
        if target is not None:
            try:
                target.click(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
                return
            except PWError as exc:
                raise ConcurUIError(
                    f"{what} 클릭 결과를 확인할 수 없어 자동 재클릭하지 않았습니다: {exc}"
                    + diagnose(page, what)) from exc
        page.wait_for_timeout(100)
    raise ConcurUIError(f"{what}을(를) 누를 수 없습니다: {last}" + diagnose(page, what))


SAVE_BUTTONS_JS = "(csv) => {" + DOM_HELPERS + """
  document.querySelectorAll('[data-auto-save]')
    .forEach(e => e.removeAttribute('data-auto-save'));
  const hidden = (b) => {
    const cls = typeof b.className === 'string' ? b.className : '';
    return /save-hidden-button/.test(cls) || /-hidden$/.test(b.getAttribute('data-nuiexp') || '');
  };
  const buttons = [...surface().querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return visible(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
  });
  const text = (b) => (b.innerText || '').trim();
  const found = [];
  for (const want of csv.split(',')) {
    for (const b of buttons) {
      if (text(b) === want && !found.includes(b)) found.push(b);
    }
  }
  // 숨긴 버튼은 뒤로 민다. 앞의 것이 눌리면 거기까지 가지도 않는다.
  found.sort((a, b) => (hidden(a) ? 1 : 0) - (hidden(b) ? 1 : 0));
  return found.map((b, i) => {
    b.setAttribute('data-auto-save', String(i));
    return '[data-auto-save="' + i + '"]';
  });
}
"""
