"""C: visit one expense, match its detail, finish/verify it, then visit the next.

The list is an ID queue and a receipt/known-duplicate hint, not a prerequisite
for parsing every amount/date. An unreadable expense does not wildcard-block
unrelated transactions. Existing field writers and per-operation journal keys
are retained; no new expenses or report submission are performed.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256

from . import attach_receipts as ar, fix_expenses as fx, console, sheet, hangul
from .concur_detail import DetailDriver, ContextChanged, guard_report
from .concur_values import clean
from .concur_workflow import RunResult
from .execution import Task, Journal, task_key, execute
from .report_session import report_key
from .target_matching import VENDOR_MIN, VENDOR_MARGIN


@dataclass
class Target:
    source: object
    slip: object = None
    entry: object = None

    def identity(self):
        s = self.source
        return [str(s.approval or ''), s.when, s.amount, clean(s.merchant).casefold()]


def targets_from(slips, entries):
    groups = defaultdict(list)
    for kind, values in [('receipt', slips), ('edit', entries)]:
        for index, source in enumerate(values):
            key = (str(source.approval), source.when, source.amount, clean(source.merchant).casefold())
            if not source.approval:
                key = ('unknown', kind, index)
            groups[key].append((kind, source))
    targets = []
    for group in groups.values():
        batches = [group] if len({k for k, _ in group}) == len(group) else [[item] for item in group]
        for batch in batches:
            by = dict(batch)
            targets.append(Target(batch[0][1], by.get('receipt'), by.get('edit')))
    return targets


def choose_target(row, targets, tolerance):
    """Match ALL local sources, including already used ones; never shift a duplicate."""
    if row.when is None or row.amount is None:
        return None, '상세 날짜·금액 미확인'
    candidates = [i for i, target in enumerate(targets)
                  if target.source.amount == row.amount and target.source.when == row.when]
    how = '날짜·금액 일치'
    if not candidates and tolerance:
        candidates = [i for i, target in enumerate(targets) if target.source.amount == row.amount
                      and abs((target.source.when - row.when).days) <= tolerance]
        how = '날짜 허용 오차·금액 일치'
    if not candidates:
        return None, '작업지/전표에 일치 거래 없음'
    if len(candidates) == 1:
        return candidates[0], how
    scored = sorted(((hangul.similarity(targets[i].source.merchant, row.vendor), i)
                     for i in candidates), reverse=True)
    if scored[0][0] >= VENDOR_MIN and scored[0][0] - scored[1][0] >= VENDOR_MARGIN:
        return scored[0][1], how + '·가맹점 구분'
    return None, '같은 날짜·금액의 원본 거래를 가맹점으로 구별하지 못함'


def known_competitor(row, target, queue, tolerance):
    """Block actual known duplicate candidates, not unknown rows treated as wildcards.

    Exact-day candidates outrank near-day candidates. A clearly better vendor
    match may distinguish known same-day/amount expenses before either is edited.
    """
    s = target.source
    others = [r for r in queue if r.expense_id and r.expense_id != row.expense_id
              and r.when is not None and r.amount == row.amount
              and abs((r.when - s.when).days) <= tolerance]
    if row.when == s.when:
        others = [r for r in others if r.when == s.when]
    elif any(r.when == s.when for r in others):
        return True
    if not others:
        return False
    own = hangul.similarity(s.merchant, row.vendor)
    return not (own >= VENDOR_MIN and all(own - hangul.similarity(s.merchant, r.vendor)
                                        >= VENDOR_MARGIN for r in others))


def binding_owners(journal, report_url):
    owners = defaultdict(set)
    if journal:
        for key, value in journal.data['tasks'].items():
            parts = key.split('|')
            if (len(parts) == 4 and parts[0] == report_url and parts[2] == 'expense'
                    and value.get('state') in ('running', 'needs_review', 'verified')):
                owners[parts[3]].add(parts[1])
    return owners


def unresolved_other_intent(journal, task):
    prefix = task.key.rsplit('|', 1)[0] + '|'
    return any(key != task.key and key.startswith(prefix) and value.get('state') in ('running', 'needs_review')
               for key, value in journal.data['tasks'].items())


def run(page, report, folder, cfg, sheet_path, apply, limit=None, again=False):
    tolerance = int(cfg['date_tolerance_days'])
    if tolerance < 0 or (limit is not None and limit < 1):
        raise ValueError('날짜 허용 오차와 처리 건수를 확인해 주세요.')
    if again:
        raise ar.AttachError('한 건씩 처리 모드에서는 기존 영수증을 유지합니다. --again 없이 실행해 주세요.')
    targets = targets_from(ar.load_manifest(folder), sheet.load(sheet_path) if sheet_path else [])
    queue = list(report.rows)
    message = (f'{report.title}\n리포트 ID: {report.key[1]}\n'
               f'목록 {len(queue)}건을 한 건씩 열어 원본 거래 {len(targets)}건과 비교합니다.\n'
               '작업지와 일치하는 경비만 수정하며, 기존 영수증은 유지합니다.\n'
               '읽기 실패·실제 중복은 해당 거래만 보류합니다.\n'
               '정확한 처리 건수는 각 경비의 상세 조회 후 결정됩니다.\n'
               '차량 마일리지 신규 생성은 포함하지 않습니다.'
               + (f'\n영수증 첨부/입력 수정은 각각 최대 {limit}건입니다.' if limit else ''))
    print(message)
    if apply and not console.confirm_action(message, '한 건씩 확인하고 반영 시작'):
        return RunResult(0, '사용자가 반영을 취소했습니다. Concur는 변경하지 않았습니다.')
    if report_key(page.url) != report.key:
        raise ContextChanged('선택한 리포트가 바뀌어 시작하지 않았습니다.')
    driver = DetailDriver(page, report.url, folder)
    journal = Journal(folder / 'concur-progress.json') if apply else None
    owners = binding_owners(journal, report.url)
    ids = Counter(r.expense_id for r in queue if r.expense_id)
    visited, matched = set(), set()
    stats = dict(inspected=0, skipped=0, held=0, receipts=0, edits=0, kept=0, limited=0)
    scheduled = dict(receipt=0, edit=0)

    for number, seed in enumerate(queue, 1):
        guard_report(page, report.url)
        prefix = f'[{number}/{len(queue)}]'
        if not seed.expense_id or ids[seed.expense_id] != 1:
            print(f'{prefix} 보류: 경비 ID 누락/중복. 다른 경비는 계속 확인합니다.')
            stats['held'] += 1
            continue
        if seed.expense_id in visited:
            continue
        visited.add(seed.expense_id)
        print(f'{prefix} 경비 상세 열기: {seed.expense_id}')
        visit = Task(task_key(report.url, seed.expense_id, 'inspection', 'detail-v1'),
                     f'상세 확인 {seed.expense_id}', None, None)
        try:
            row = driver.inspect(seed)
            stats['inspected'] += 1
            index, how = choose_target(row, targets, tolerance)
            if index is None:
                if how == '작업지/전표에 일치 거래 없음':
                    stats['skipped'] += 1
                else:
                    stats['held'] += 1
                print(f'{prefix} 건드리지 않음: {how}')
                if journal:
                    journal.set(visit, 'pending', how)
                continue
            target = targets[index]
            matched.add(index)
            visit = Task(task_key(report.url, row.expense_id, 'expense', target.identity()),
                         f'{row.when} {row.amount:,}원 · 한 건 처리', None, None)
            binding = visit.key.rsplit('|', 1)[1]
            if (owners[binding] - {row.expense_id}) or known_competitor(row, target, queue, tolerance):
                print(f'{prefix} 보류: 동일 원본에 다른 경비 후보/처리 기록이 있습니다.')
                stats['held'] += 1
                if journal:
                    journal.set(visit, 'pending', '실제 중복 경비 또는 기존 처리 대상과 충돌')
                continue
            owners[binding].add(row.expense_id)
            print(f'{prefix} 작업지 일치: {row.when} {row.amount:,}원 ({how})')
            plans = fx.plans_from_matches(cfg, [(target.entry, row, how)], [])[0] if target.entry else []
            tasks, receipt_unclear, limited = [], False, False
            if target.slip:
                receipt = driver.receipt_row(row)
                if receipt.has_receipt is True:
                    stats['kept'] += 1
                    print(f'{prefix} 기존 영수증 유지 · PDF 업로드 안 함')
                elif receipt.has_receipt is None:
                    receipt_unclear = True
                    print(f'{prefix} 영수증 상태 미확인 · 업로드 보류, 입력 수정은 별도 확인')
                elif limit is not None and scheduled['receipt'] >= limit:
                    limited = True
                else:
                    slip = target.slip
                    digest = sha256(slip.path.read_bytes()).hexdigest()
                    def attach(s=slip, r=row, expected=digest):
                        if sha256(s.path.read_bytes()).hexdigest() != expected:
                            raise ar.AttachError('영수증 파일이 변경되어 첨부하지 않았습니다.')
                        driver.attach(s, r)
                    tasks.append(('receipt', Task(
                        task_key(report.url, row.expense_id, 'receipt', [slip.path.name, digest, False]),
                        f'영수증 {slip.path.name}', attach,
                        lambda s=slip, r=row: driver.receipt_present(r, s))))
                    scheduled['receipt'] += 1
            if plans:
                plan = plans[0][0]
                if limit is not None and scheduled['edit'] >= limit:
                    limited = True
                else:
                    intent = asdict(plan)
                    intent.pop('row'); intent.pop('type_code')
                    tasks.append(('edit', Task(task_key(report.url, row.expense_id, 'edit', intent),
                        f'{row.when} {row.amount:,}원 · {plan.summary()}',
                        lambda p=plan: driver.apply_edit(p), lambda p=plan: driver.verify_edit(p))))
                    scheduled['edit'] += 1
            if not apply:
                for _, task in tasks:
                    print(f'{prefix} 미리보기: {task.label}')
                continue
            journal.set(visit, 'running', '원본 연결 고정 · 경비별 작업 시작')
            failed = receipt_unclear
            for kind, task in tasks:
                if unresolved_other_intent(journal, task):
                    journal.set(task, 'pending', '같은 경비의 이전 미확정 작업이 있어 자동 전송하지 않음')
                    failed = True
                    break
                status = execute([task], journal, driver.guard)
                guard_report(page, report.url)
                if status:
                    failed = True
                    # An uncertain receipt write must not be saved again by the edit step.
                    break
                stats['receipts' if kind == 'receipt' else 'edits'] += 1
            if failed:
                stats['held'] += 1
            elif limited:
                stats['limited'] += 1
            journal.set(visit, 'needs_review' if failed else 'pending' if limited else 'verified',
                        '일부 작업 확인 필요' if failed else '처리 건수 제한' if limited else '필요 작업 결과 확인 · 기존 영수증 유지')
            print(f'{prefix} ' + ('일부 보류' if failed else '건수 제한' if limited else '확인 완료') + ' → 다음 경비')
        except ContextChanged:
            raise
        except Exception as exc:
            # Do not navigate to the next ID to hide a report/login context change.
            guard_report(page, report.url)
            stats['held'] += 1
            print(f'{prefix} 이 경비만 보류: {exc}')
            if journal:
                journal.set(visit, 'needs_review' if journal.state(visit.key) in ('running', 'needs_review', 'verified') else 'pending', str(exc))

    missing = len(targets) - len(matched)
    for i, target in enumerate(targets):
        if i not in matched:
            source = target.source
            print(f'  ! 처리 대상 미확인: {source.when} {source.amount:,}원 · 상세 조회에서 연결하지 못했습니다.')
    summary = (f"상세 확인 {stats['inspected']}건 · 기존 영수증 유지 {stats['kept']}건 · "
               f"첨부 확인 {stats['receipts']}건 · 입력 확인 {stats['edits']}건 · "
               f"일치 거래 없음 {stats['skipped']}건 · 보류 {stats['held']}건 · 원본 미연결 {missing}건"
               + (f" · 건수 제한 {stats['limited']}건" if stats['limited'] else ''))
    if not apply:
        summary = '미리보기 — Concur와 진행 기록은 변경하지 않았습니다. ' + summary
    print(summary)
    result = RunResult(int(bool(stats['held'] or missing)) if apply else 0, summary)
    result.stats = {**stats, 'missing_sources': missing}
    return result
