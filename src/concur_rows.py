"""기존 data-row 훅과 실제 열 머리글로만 경비 목록을 읽는다."""

READ_ROWS_JS = r'''() => {
  const text = el => el ? (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ') : '';
  const visible = e => {
    if (!e || e.closest('[hidden], [aria-hidden="true"]')) return false;
    const box = e.getBoundingClientRect(), s = getComputedStyle(e);
    return box.width > 0 && box.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };
  const rows = [...document.querySelectorAll('[data-testid="data-row"]')].filter(visible);
  const aliases = {
    'date-cell': /^(날짜|거래일|Date|Transaction Date)$/i,
    'amount-cell': /^(금액|요청됨|Amount|Requested|Requested Amount)$/i,
    'vendor-name': /^(공급업체 상세 정보|가맹점|Vendor|Vendor Details|Merchant)$/i,
    'expense-type-cell': /^(경비 유형|Expense Type)$/i
  };
  const cell = (r, hook) => {
    const direct = r.querySelector('[data-nuiexp="' + hook + '"]');
    if (direct && text(direct)) return direct;
    const root = r.closest('[role="grid"], [role="table"], table');
    if (!root || !aliases[hook]) return direct;
    const headers = [...root.querySelectorAll('[role="columnheader"], th')];
    const matches = headers.filter(h => aliases[hook].test(text(h)));
    if (matches.length !== 1) return direct;
    const h = matches[0], col = h.getAttribute('aria-colindex');
    const cells = [...r.querySelectorAll('[role="cell"], [role="gridcell"], td')];
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
    const root = r.closest('[role="grid"], [role="table"], table');
    const expected = root ? +(root.getAttribute('aria-rowcount') || '-1') : -1;
    const headerRows = root ? [...root.querySelectorAll('[role="row"]')]
      .filter(e => !!e.querySelector('[role="columnheader"]')).length : 0;
    const sameRootRows = rows.filter(row => row.closest('[role="grid"], [role="table"], table') === root);
    const busy = root && root.getAttribute('aria-busy') === 'true';
    const incomplete = expected > 0 && sameRootRows.length < expected - headerRows;
    return {
      index: i,
      id: r.id || r.getAttribute('data-row-key') || null,
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
