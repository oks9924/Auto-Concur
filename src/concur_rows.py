"""경비 행의 표시 셀과 검증된 표 구조로 목록을 읽는다."""

READ_ROWS_JS = r'''() => {
  const text = el => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : '';
  const ROW = '[data-testid="data-row"], [role="row"], tr';
  const ROOT = '[role="grid"], [role="table"], table';
  const CELLS = '[role="cell"], [role="gridcell"], td';
  const rootOf = r => r.closest(ROOT);
  const owned = (r, selector) => [...r.querySelectorAll(selector)]
    .filter(e => e.closest(ROW) === r);
  const headersOf = root => root ? [...root.querySelectorAll('[role="columnheader"], th')]
    .filter(h => h.closest(ROOT) === root) : [];
  const visibleBox = e => {
    if (!e) return false;
    for (let n = e; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (n.hidden || n.getAttribute('aria-hidden') === 'true' || s.display === 'none'
          || s.visibility === 'hidden' || s.visibility === 'collapse'
          || s.contentVisibility === 'hidden') return false;
    }
    const box = e.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  // display:contents/좁은 레이아웃은 행 자체의 박스가 없어도 셀이 보일 수 있다.
  // 뷰포트 밖의 행은 스크롤로 볼 수 있으므로 위치만으로 버리지 않는다.
  const visibleRow = r => visibleBox(r)
    || owned(r, CELLS + ', [data-nuiexp]').some(visibleBox);
  const aliases = {
    'date-cell': /^(날짜|거래일|Date|Transaction Date)$/i,
    'amount-cell': /^(금액|요청됨|Amount|Requested|Requested Amount)$/i,
    'vendor-name': /^(공급업체 상세 정보|가맹점|Vendor|Vendor Details|Merchant)$/i,
    'expense-type-cell': /^(경비 유형|Expense Type)$/i
  };
  const directField = (r, hook) => owned(r, '[data-nuiexp="' + hook + '"]')[0];
  const hasExpenseHooks = r => ['date-cell', 'amount-cell', 'expense-type-cell']
    .filter(hook => directField(r, hook)).length >= 2;
  const expenseTable = root => ['date-cell', 'amount-cell', 'expense-type-cell']
    .every(hook => headersOf(root).filter(h => aliases[hook].test(text(h))).length === 1);
  // 보조 경로의 임의 DOM id는 경비 ID로 추측하지 않는다.
  const linkedId = r => {
    const report = location.pathname.match(/^\/nui\/expense\/reports\/([^/]+)(?:\/expenses)?\/?$/);
    if (!report) return null;
    const ids = owned(r, 'a[href]').map(a => {
      let u;
      try { u = new URL(a.getAttribute('href'), location.href); } catch (_) { return null; }
      const m = u.pathname.match(/^\/nui\/expense\/reports\/([^/]+)\/expenses\/([^/]+)\/?$/);
      return u.origin === location.origin && m && m[1] === report[1] ? m[2] : null;
    }).filter(Boolean);
    return new Set(ids).size === 1 ? ids[0] : null;
  };
  const legacyRow = r => r.matches('[data-testid="data-row"]');
  const rowId = r => legacyRow(r) || hasExpenseHooks(r)
    ? r.id || r.getAttribute('data-row-key') || linkedId(r)
    : r.getAttribute('data-row-key') || linkedId(r);
  const rows = [...document.querySelectorAll(ROW)].filter(r => {
    if (r.closest('thead, tfoot') || owned(r, '[role="columnheader"], th').length
        || !visibleRow(r)) return false;
    if (legacyRow(r)) return true;
    if (r.closest('[data-testid="data-row"]')) return false;
    const root = rootOf(r), cells = owned(r, CELLS);
    if (hasExpenseHooks(r)) return true;
    if (!root) return false;
    if (!expenseTable(root) || cells.length < 2) return false;
    // 표 머리글이 검증된 경우만 보조 탐색. 합계는 데이터로 취급하지 않는다.
    if (!rowId(r) && /^(합계|총계|소계|Total|Grand Total|Subtotal)$/i.test(text(cells[0]))) return false;
    return true;
  });
  const cell = (r, hook) => {
    const direct = directField(r, hook);
    if (direct && text(direct)) return direct;
    const root = rootOf(r);
    if (!root || !aliases[hook]) return direct;
    const headers = headersOf(root);
    const matches = headers.filter(h => aliases[hook].test(text(h)));
    if (matches.length !== 1) return direct;
    const h = matches[0], col = h.getAttribute('aria-colindex');
    const cells = owned(r, CELLS);
    if (col) return cells.find(c => c.getAttribute('aria-colindex') === col) || direct;
    if (cells.length === headers.length && headers.every(h => +(h.getAttribute('colspan') || 1) === 1)
        && cells.every(c => +(c.getAttribute('colspan') || 1) === 1)) {
      return cells[headers.indexOf(h)] || direct;
    }
    return direct;
  };
  const pick = (r, hook) => text(cell(r, hook));
  return rows.map((r, i) => {
    const d = cell(r, 'date-cell'), amount = cell(r, 'amount-cell');
    const receiptCell = r.querySelector('[data-nuiexp="receipts-cell"], [class*="receipt-cell"]');
    const receiptHit = receiptCell && receiptCell.querySelector('[data-nuiexp^="receipt-thumbnail-button"]');
    const root = rootOf(r);
    const expected = root ? +(root.getAttribute('aria-rowcount') || '-1') : -1;
    const headerRows = root ? new Set(headersOf(root).map(h => h.closest(ROW)).filter(Boolean)).size : 0;
    const sameRootRows = rows.filter(row => rootOf(row) === root);
    const busy = root && root.getAttribute('aria-busy') === 'true';
    const incomplete = expected > 0 && sameRootRows.length < expected - headerRows;
    return {
      index: i,
      id: rowId(r) || null,
      date: text(d),
      dateISO: d ? (d.getAttribute('datetime') || d.querySelector('time[datetime]')?.getAttribute('datetime') || '') : '',
      dateLabel: d ? (d.getAttribute('aria-label') || d.getAttribute('title') || '') : '',
      amount: pick(r, 'amount-cell'),
      vendor: text(cell(r, 'vendor-name')),
      expenseType: text(cell(r, 'expense-type-cell')),
      receipt: receiptCell ? !!receiptHit : null,
      receiptFile: receiptHit ? (receiptHit.getAttribute('data-nuiexp') || '').replace('receipt-thumbnail-button-', '') : '',
      label: text(r.querySelector('[class*="screen-reader-only"]')),
      readProblem: busy ? '목록 로딩 중' : incomplete ? '표시된 행이 전체 행 수보다 적음' : ''
    };
  });
}'''


def rows_complete(rows):
    ids = [r.expense_id for r in rows]
    return bool(rows) and len(ids) == len(set(ids)) and all(
        r.expense_id and r.when is not None and r.amount is not None
        and not getattr(r, 'read_problem', '') for r in rows)


def readiness_detail(rows):
    if not rows:
        return '경비 데이터 행 0건. 리포트의 경비 목록을 연 상태인지 확인해 주세요.'
    counts = {'경비 ID': sum(not r.expense_id for r in rows),
              '날짜': sum(r.when is None for r in rows),
              '금액': sum(r.amount is None for r in rows)}
    missing = ', '.join(f'{key} {count}건' for key, count in counts.items() if count)
    notes = sorted({getattr(r, 'read_problem', '') for r in rows} - {''})
    ids = [r.expense_id for r in rows]
    if len(ids) != len(set(ids)):
        notes.append('중복 경비 ID')
    return f'경비 {len(rows)}건' + (f' · 읽지 못한 값: {missing}' if missing else '') + (
        ' · ' + ', '.join(notes) if notes else '')


def rows_observed(rows):
    """목록 로딩/누락은 차단하되 안정된 불완전 행은 매칭 단계에 그대로 넘긴다."""
    return bool(rows) and not any(getattr(r, 'read_problem', '') for r in rows)


def snapshot_signature(rows):
    """해석값뿐 아니라 원문 변화도 재렌더링/대상 변경으로 감지한다."""
    return tuple((r.expense_id, r.when, r.amount, r.vendor, r.expense_type,
                  r.has_receipt, r.receipt_file, r.raw_date, r.raw_amount,
                  getattr(r, 'read_problem', '')) for r in rows)
