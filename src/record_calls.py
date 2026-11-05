"""Concur 화면이 실제로 주고받는 호출의 '모양'만 기록한다. 값은 적지 않는다.

지금 C단계는 화면에 그려진 글자를 읽고 그 위를 클릭한다. 그래서 날짜 서식,
숨은 버튼, 패널이 클릭을 가로채는 것 같은 표현 계층 문제가 전부 우리 문제가
된다. 한 계층 아래(SPA가 쓰는 JSON 호출)로 내려갈 수 있는지 보려면 먼저
그 계층이 있는지 확인해야 한다. 이 도구가 그걸 확인한다.

    python -m src.record_calls

**이 도구는 아무것도 바꾸지 않는다.** 호출을 재생하지도, 저장을 누르지도
않는다. 사람이 평소처럼 손으로 작업하고, 그동안 오간 호출의 모양만 적는다.

## 값을 적지 않는 이유와 방법

기록은 페이지 안(JS)에서 모양으로 바꾼 뒤에 밖으로 나온다. 즉 실제 거래
값은 파이썬에 닿지도 않는다. 적는 것은 이런 것이다.

    {"method":"PUT","host":"eu2.concursolutions.com",
     "path":"/nui/expense/entry/{id}",
     "reqBody":{"transactionDate":"str:date-iso","transactionAmount":"int"}}

`transactionDate` 가 `str:date-iso` 인지 `str:date-slash` 인지가 우리가 알아야
할 전부다. `2026-09-07` 이 무슨 거래였는지는 알 필요가 없다. 가맹점명·금액·
승인번호·참석자 이름은 전부 `str:len9-32` 같은 길이 구간으로만 남는다.
`downloads/` 와 `manifest.csv` 를 커밋하지 않는 것과 같은 이유다.

결과는 `inspect-out/` 에 떨어진다(이미 gitignore 됨).

## 같이 켜두면 안 되는 것

평소 C단계와 **같은 브라우저 프로필**을 쓴다. 이미 로그인돼 있어서 편하지만,
둘을 동시에 띄우면 Chromium이 프로필을 못 열고 죽는다. 하나씩 돌린다.
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Error as PWError

from . import browser, console, paths

PROFILE_DIR = paths.at('browser-profile')
OUT_DIR = paths.at('inspect-out')
START_URL = 'https://travel.siemens.cloud'

# 사람이 손으로 할 일. 단계마다 Enter를 누르면 기록에 표시가 들어가서
# 어느 호출이 어느 동작의 것인지 나중에 가를 수 있다. 건너뛰려면 그냥 Enter.
STEPS = [
    '로그인하고 경비 리포트 목록 열기',
    '경비 하나 열기 (상세 화면)',
    '비즈니스 목적 또는 코멘트를 바꿔서 저장',
    '참석자 한 명 추가해서 저장',
    '숙박 경비에 항목별 명세 입력해서 저장',
    '영수증 하나 첨부',
]

# 페이지 안에서 fetch/XHR을 감싼다. 응답을 clone해서 읽으므로 원래 호출의
# 본문은 건드리지 않는다. 기록이 실패해도 원래 동작은 그대로 돌아가야 한다 -
# 그래서 모든 구간이 try/catch이고, 원래 함수의 반환값을 그대로 돌려준다.
RECORD_JS = r"""
(() => {
  if (window.__acRec) return;
  window.__acRec = [];
  const CAP = 600, DEPTH = 6, KEYS = 40, BODY = 400000;

  // 문자열은 '어떤 종류인가'만 남긴다. 내용은 남기지 않는다.
  // 날짜가 기계값으로 오는지가 이 조사의 핵심이라 날짜 꼴을 세분한다.
  const KINDS = [
    [/^\d{4}-\d{2}-\d{2}$/, 'date-iso'],
    [/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/, 'datetime-iso'],
    [/^\d{1,2}\/\d{1,2}\/\d{4}$/, 'date-slash'],
    [/^\d{4}\/\d{1,2}\/\d{1,2}$/, 'date-slash-ymd'],
    [/^\d{1,2}\.\d{1,2}\.\d{4}$/, 'date-dot'],
    [/^\d{4}\.\d{1,2}\.\d{1,2}$/, 'date-dot-ymd'],
    [/^\d{8}$/, 'digits8'],
    [/^-?\d+$/, 'digits'],
    [/^-?\d+\.\d+$/, 'decimal'],
    [/^-?\d{1,3}([,.]\d{3})+([.,]\d+)?$/, 'number-grouped'],
    [/^[0-9A-Fa-f]{16,}$/, 'hex-id'],
    [/^[A-Z]{2,3}$/, 'upper-code'],
    [/^(true|false)$/i, 'boolish'],
    [/^\s*$/, 'blank'],
  ];
  const bucket = n => n === 0 ? '0' : n <= 8 ? '1-8' : n <= 32 ? '9-32'
                    : n <= 128 ? '33-128' : '128+';
  const strKind = s => {
    for (const [re, name] of KINDS) if (re.test(s)) return 'str:' + name;
    return 'str:len' + bucket(s.length);
  };
  const shape = (v, d) => {
    if (v === null) return 'null';
    if (v === undefined) return 'undefined';
    if (d > DEPTH) return '...';
    const t = typeof v;
    if (t === 'boolean') return 'bool';
    if (t === 'number') return Number.isInteger(v) ? 'int' : 'float';
    if (t === 'string') return strKind(v);
    if (Array.isArray(v)) {
      return v.length ? {'[n]': v.length, '[0]': shape(v[0], d + 1)} : {'[n]': 0};
    }
    if (t === 'object') {
      const out = {}; const keys = Object.keys(v);
      keys.slice(0, KEYS).forEach(k => { out[k] = shape(v[k], d + 1); });
      if (keys.length > KEYS) out['...'] = keys.length - KEYS;
      return out;
    }
    return t;
  };

  // 경로의 ID는 {id}/{n} 으로 가린다. 알아야 할 것은 경로의 모양이다.
  const place = raw => {
    let u;
    try { u = new URL(raw, location.href); }
    catch (e) { return {host: '?', path: String(raw).slice(0, 120), query: []}; }
    const path = u.pathname.split('/').map(s => {
      if (!s) return s;
      if (/^\d+$/.test(s)) return '{n}';
      if (/^[0-9A-Fa-f-]{8,}$/.test(s)) return '{id}';
      if (s.length >= 12 && /\d/.test(s)) return '{id}';
      return s;
    }).join('/');
    let query = [];
    try { query = [...new Set([...u.searchParams.keys()])].sort(); } catch (e) {}
    return {host: u.host, path, query};
  };

  // Concur는 GraphQL을 쓴다. 호출이 전부 한 주소로 가는 POST라서 경로는
  // 아무것도 알려주지 않는다. 무엇을 하는 호출인지는 operationName과 질의문에
  // 있고, 그 둘은 스키마(필드 이름)이지 거래 값이 아니다 - 값은 variables로
  // 따로 간다. 그래서 이 둘만 그대로 남긴다.
  const IDENT = /^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/;

  // 다만 클라이언트가 값을 질의문에 박아 보낼 수도 있다. 문자열·숫자
  // 리터럴을 지워서 그 경우에도 값이 새지 않게 한다.
  const gqlDoc = s => String(s)
    .replace(/"(?:\\.|[^"\\])*"/g, '"?"')
    .replace(/\b\d[\d_.eE+-]*\b/g, '?')
    .replace(/\s+/g, ' ').trim().slice(0, 4000);

  // 구형 .NET 라우터(expenseRouter.ashx)는 {action, method, data} 로 부른다.
  // GraphQL의 operationName과 같은 자리다 - 무엇을 하는 호출인지가 거기 있다.
  const NAMES = ['operationName', 'action', 'method'];

  const describe = v => {
    const out = shape(v, 0);
    if (v && typeof v === 'object' && !Array.isArray(v) && out && typeof out === 'object') {
      NAMES.forEach(key => {
        if (typeof v[key] === 'string' && IDENT.test(v[key])) out[key] = v[key];
      });
      if (typeof v.query === 'string') out.query = gqlDoc(v.query);
    }
    return out;
  };

  const parsed = text => { try { return {ok: true, v: JSON.parse(text)}; } catch (e) { return {ok: false}; } };

  // 요약에서 호출을 가를 이름. GraphQL이면 작전 이름, 아니면 없다.
  const opName = text => {
    if (typeof text !== 'string') return null;
    const got = parsed(text);
    if (!got.ok) return null;
    const first = Array.isArray(got.v) ? got.v[0] : got.v;
    if (!first || typeof first !== 'object') return null;
    const pick = key => typeof first[key] === 'string' && IDENT.test(first[key]) ? first[key] : null;
    const name = pick('operationName');
    if (name) return name;
    // 구형 라우터는 action.method 로 하나를 가린다.
    const action = pick('action'), method = pick('method');
    return method ? (action ? action + '.' + method : method) : null;
  };

  const fromText = (text, type) => {
    if (text == null) return null;
    if (text.length > BODY) return 'too-large:' + text.length;
    if (/multipart/i.test(type || '')) return 'multipart:' + text.length;
    if (/x-www-form-urlencoded/i.test(type || '')) {
      try { return {'form-keys': [...new Set([...new URLSearchParams(text).keys()])].sort()}; }
      catch (e) { return 'form-unreadable'; }
    }
    const got = parsed(text);
    if (got.ok) {
      return Array.isArray(got.v)
        ? {'[n]': got.v.length, '[0]': got.v.length ? describe(got.v[0]) : 'null'}
        : describe(got.v);
    }
    return /json/i.test(type || '') ? 'json-parse-failed' : 'text:len' + bucket(text.length);
  };

  const reqBody = (body, type) => {
    if (body == null) return null;
    try {
      if (typeof body === 'string') return fromText(body, type);
      if (typeof FormData !== 'undefined' && body instanceof FormData) {
        const keys = []; for (const k of body.keys()) keys.push(k);
        return {'form-data-keys': [...new Set(keys)].sort()};
      }
      if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) {
        return {'form-keys': [...new Set([...body.keys()])].sort()};
      }
      if (typeof Blob !== 'undefined' && body instanceof Blob) return 'blob:' + body.size;
      if (body instanceof ArrayBuffer) return 'binary:' + body.byteLength;
      return 'body:' + (typeof body);
    } catch (e) { return 'body-unreadable'; }
  };

  const push = rec => {
    try {
      rec.at = new Date().toISOString();
      if (window.__acRec.length < CAP) window.__acRec.push(rec);
    } catch (e) {}
  };

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function (input, init) {
      let rec = null;
      try {
        const isRequest = typeof Request !== 'undefined' && input instanceof Request;
        const raw = isRequest ? input.url : String(input);
        const method = String((init && init.method) || (isRequest && input.method) || 'GET').toUpperCase();
        const headers = new Headers((init && init.headers) || (isRequest ? input.headers : undefined) || {});
        const names = []; headers.forEach((v, k) => names.push(String(k).toLowerCase()));
        const body = init && 'body' in init ? init.body : undefined;
        const sent = body !== undefined ? reqBody(body, headers.get('content-type') || '')
                   : isRequest ? 'in-request-object' : null;
        rec = Object.assign({via: 'fetch', method}, place(raw),
                            {op: opName(body), reqHeaders: [...new Set(names)].sort(), reqBody: sent});
      } catch (e) { rec = null; }
      const out = originalFetch.apply(this, arguments);
      try {
        if (rec) {
          Promise.resolve(out).then(res => {
            rec.status = res.status;
            const type = res.headers.get('content-type') || '';
            rec.resType = type.split(';')[0];
            return res.clone().text().then(
              t => { rec.resBody = fromText(t, type); },
              () => { rec.resBody = 'unreadable'; });
          }).catch(() => { rec.status = rec.status || 'network-failed'; })
            .then(() => push(rec), () => push(rec));
        }
      } catch (e) {}
      return out;
    };
  }

  const XO = XMLHttpRequest.prototype.open;
  const XS = XMLHttpRequest.prototype.send;
  const XH = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (method, url) {
    try {
      this.__ac = Object.assign({via: 'xhr', method: String(method || 'GET').toUpperCase()},
                                place(url), {reqHeaders: [], ctype: ''});
    } catch (e) { this.__ac = null; }
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    try {
      if (this.__ac) {
        this.__ac.reqHeaders.push(String(name).toLowerCase());
        if (/^content-type$/i.test(name)) this.__ac.ctype = String(value);
      }
    } catch (e) {}
    return XH.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      const rec = this.__ac, xhr = this;
      if (rec) {
        rec.reqBody = reqBody(body, rec.ctype);
        rec.op = opName(body);
        rec.reqHeaders = [...new Set(rec.reqHeaders)].sort();
        delete rec.ctype;
        this.addEventListener('loadend', () => {
          try {
            rec.status = xhr.status;
            const type = xhr.getResponseHeader('content-type') || '';
            rec.resType = type.split(';')[0];
            let text = null;
            const kind = xhr.responseType;
            if (kind === '' || kind === 'text') text = xhr.responseText;
            else if (kind === 'json') text = JSON.stringify(xhr.response);
            rec.resBody = text == null ? 'responseType:' + kind : fromText(text, type);
          } catch (e) { rec.resBody = 'unreadable'; }
          push(rec);
        });
      }
    } catch (e) {}
    return XS.apply(this, arguments);
  };
})();
"""

DRAIN_JS = "() => { const a = window.__acRec; return a && a.length ? a.splice(0, a.length) : []; }"


def drain(ctx) -> list[dict]:
    """열려 있는 모든 창·프레임에서 기록을 걷어온다."""
    found = []
    for page in list(ctx.pages):
        if page.is_closed():
            continue
        for frame in list(page.frames):
            try:
                found.extend(frame.evaluate(DRAIN_JS) or [])
            except PWError:
                pass  # 이동 중인 프레임은 다음 회차에 다시 본다
    return found


# 화면과 무관한 잡음. 기록에는 남기고 화면에만 안 띄운다 - 사람이 자기 동작과
# 호출을 눈으로 맞출 수 있어야 하는데, 텔레메트리가 그 사이를 메우면 못 맞춘다.
NOISE = ('events.data.microsoft.com', 'qualtrics.com', 'trustarc.com',
         '/api/logs', '/log/metric', '/newMessageCount')


def noisy(rec: dict) -> bool:
    where = f"{rec.get('host', '')}{rec.get('path', '')}"
    return any(mark in where for mark in NOISE)


def where_of(rec: dict) -> str:
    """무엇을 하는 호출인가. GraphQL이면 경로가 아니라 작전 이름이 답이다."""
    op = rec.get('op')
    return f"{rec.get('host', '?')}{rec.get('path', '')}" + (f"  [{op}]" if op else '')


def label(rec: dict) -> str:
    query = rec.get('query') or []
    return (f"{rec.get('method', '?'):6} {rec.get('status', '-')!s:>3} {where_of(rec)}"
            + (f" ?{','.join(query)}" if query else ''))


def summary(records: list[dict]) -> list[str]:
    """같은 호출을 묶어서 센다. 쓰기(비 GET)를 위에 둔다.

    GraphQL은 전부 한 주소로 가는 POST라서 경로로 묶으면 78건이 한 줄이 된다.
    작전 이름으로 갈라야 무엇을 하는 호출인지 보인다.
    """
    groups: Counter = Counter()
    for rec in records:
        if 'method' not in rec:
            continue
        groups[(rec.get('method'), where_of(rec), str(rec.get('status', '-')))] += 1
    rows = sorted(groups.items(), key=lambda kv: (kv[0][0] == 'GET', kv[0][1]))
    return [f"  {method:6} {status:>3} {where}  {count}회"
            for (method, where, status), count in rows]


def _read_lines(sink: queue.Queue) -> None:
    """Enter/q를 받는다. 별도 스레드다 - 기다리는 동안에도 기록은 계속 걷는다."""
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            sink.put('q')
            return
        sink.put(line.strip().lower())
        if line.strip().lower() == 'q':
            return


def record(url: str, channel: str | None = None) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"calls-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    records: list[dict] = []

    with sync_playwright() as pw:
        ctx = browser.launch(pw, PROFILE_DIR, only=channel,
                             accept_downloads=True, locale='ko-KR')
        # 이동 전에 걸어야 한다. 모든 문서에 다시 주입된다.
        ctx.add_init_script(RECORD_JS)
        browser.open_first(ctx, url)

        print('\n' + '=' * 68)
        print('  호출 모양만 기록합니다. 이 도구는 아무것도 바꾸지 않습니다.')
        print('  평소처럼 손으로 작업해 주세요. 단계를 끝낼 때마다 Enter를 누르면')
        print('  기록에 표시가 들어가서 어느 호출이 어느 동작의 것인지 가릴 수 있습니다.')
        print('=' * 68)
        for i, step in enumerate(STEPS, 1):
            print(f'  {i}. {step}')
        print('\n  (해당 없는 단계는 그냥 Enter로 넘기세요. 끝내려면 q + Enter)\n')

        keys: queue.Queue = queue.Queue()
        threading.Thread(target=_read_lines, args=(keys,), daemon=True).start()

        step = 0
        print(f'[1/{len(STEPS)}] {STEPS[0]} ... 끝나면 Enter')
        with target.open('w', encoding='utf-8') as out:
            def write(item: dict) -> None:
                records.append(item)
                out.write(json.dumps(item, ensure_ascii=False) + '\n')
                out.flush()

            write({'marker': STEPS[0], 'step': 1,
                   'note': 'shape only; no transaction values are recorded'})
            stop = False
            while not stop:
                for rec in drain(ctx):
                    write(rec)
                    if rec.get('method') != 'GET' and not noisy(rec):
                        print('    ' + label(rec))
                while not keys.empty():
                    if keys.get() == 'q':
                        stop = True
                        break
                    gets = sum(1 for r in records if r.get('method') == 'GET')
                    writes = sum(1 for r in records
                                 if r.get('method') not in (None, 'GET'))
                    print(f'  (지금까지 읽기 {gets}건 · 쓰기 {writes}건)')
                    step += 1
                    if step >= len(STEPS):
                        print('\n단계를 모두 지났습니다. 더 해보셔도 됩니다. 끝내려면 q + Enter')
                        step = len(STEPS) - 1
                        continue
                    write({'marker': STEPS[step], 'step': step + 1})
                    print(f'[{step + 1}/{len(STEPS)}] {STEPS[step]} ... 끝나면 Enter')
                if stop:
                    break
                try:
                    live = [p for p in ctx.pages if not p.is_closed()]
                    if not live:
                        print('\n브라우저 창이 닫혔습니다. 기록을 마칩니다.')
                        break
                    live[0].wait_for_timeout(400)
                except PWError:
                    print('\n브라우저와의 연결이 끊어졌습니다. 여기까지 기록했습니다.')
                    break
            for rec in drain(ctx):  # 닫기 전에 남은 것
                write(rec)
        try:
            ctx.close()
        except PWError:
            pass

    calls = [r for r in records if 'method' in r]
    print(f'\n호출 {len(calls)}건을 기록했습니다: {target}')
    print('\n== 엔드포인트 요약 (쓰기 먼저) ==')
    for line in summary(records) or ['  (기록된 호출이 없습니다)']:
        print(line)
    print('\n값은 적지 않았습니다. 날짜·금액·이름은 종류와 길이 구간으로만 남습니다.')
    return target


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description='Concur 호출의 모양만 기록합니다 (변경 없음)')
    ap.add_argument('--url', default=START_URL, help='처음 열 주소입니다')
    ap.add_argument('--channel', choices=('chrome', 'msedge', 'chromium'),
                    help='SSO가 번들 Chromium을 막을 때 설치된 브라우저를 씁니다')
    args = ap.parse_args()
    record(args.url, args.channel)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
