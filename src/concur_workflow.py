"""C단계: 준비 → 계획 → 시작 확인 → 대상 재검증 → 실행 → 결과 검증."""
from dataclasses import asdict, dataclass
from hashlib import sha256

from . import attach_receipts as ar, fix_expenses as fx, console
from .concur_driver import Driver
from .execution import Task, Journal, task_key, execute
from .report_session import revalidate


@dataclass
class WorkPlan:
    tasks: list
    receipts: int
    edits: int
    skipped_receipts: int
    unmatched: list


class RunResult(int):
    """CLI 종료 코드를 유지하면서 GUI에 정확한 결과 문구를 전달한다."""
    def __new__(cls, code, summary):
        obj = super().__new__(cls, code)
        obj.summary = summary
        return obj


def unmatched_transactions(receipts, edits):
    groups = {}
    for stage, entries in [('영수증', receipts), ('입력', edits)]:
        for index, (entry, why) in enumerate(entries):
            # 승인번호가 없는 서로 다른 거래를 날짜·금액만으로 합치지 않는다.
            key = (entry.when, entry.amount, entry.approval or (stage, index))
            item = groups.setdefault(key, [entry, []])
            item[1].append(f'{stage}: {why}')
    return [f'{entry.when} {entry.amount:,}원 · ' + ' / '.join(reasons)
            for entry, reasons in groups.values()]


def build_plan(report, folder, cfg, sheet_path, limit=None, again=False, driver=None):
    tasks, unmatched = [], []
    slips = ar.load_manifest(folder)
    pairs, missed = ar.match(slips, list(report.rows), int(cfg['date_tolerance_days']))
    skipped = sum(bool(row.has_receipt) for _, row, _ in pairs) if not again else 0
    pairs = [(s, r, how) for s, r, how in pairs if again or r.has_receipt is not True]
    pairs = pairs[:limit] if limit else pairs
    for slip, row, how in pairs:
        digest = sha256(slip.path.read_bytes()).hexdigest()
        key = task_key(report.url, row.expense_id, 'receipt', [slip.path.name, digest, again])
        def attach_unchanged(s=slip, r=row, expected=digest):
            if sha256(s.path.read_bytes()).hexdigest() != expected:
                raise ar.AttachError('계획 확인 후 영수증 파일이 변경되어 첨부하지 않았습니다.')
            driver.attach(s, r)
        tasks.append(Task(key, f'영수증 {slip.path.name} ({how})',
            attach_unchanged,
            lambda s=slip, r=row: driver.receipt_present(r, s, again)))
    paired, missed_edits = [], []
    if sheet_path:
        paired, _, missed_edits = fx.plans_from_sheet(cfg, list(report.rows), sheet_path,
                                                     int(cfg['date_tolerance_days']))
    unmatched = unmatched_transactions(missed, missed_edits)
    paired = paired[:limit] if limit else paired
    for plan, how, entry in paired:
        intent = asdict(plan)
        intent.pop('row')
        intent.pop('type_code')  # 이미 바뀐 유형 때문에 재실행의 작업 식별자가 바뀌지 않게 한다.
        key = task_key(report.url, plan.row.expense_id, 'edit', intent)
        tasks.append(Task(key, f'{plan.row.when} {plan.row.amount:,}원 · {plan.summary()}',
            lambda p=plan: driver.apply_edit(p), lambda p=plan: driver.verify_edit(p)))
    return WorkPlan(tasks, len(pairs), len(paired), skipped, unmatched)


def run(page, report, folder, cfg, sheet_path, apply, limit=None, again=False):
    driver = Driver(page, report.url, folder)
    plan = build_plan(report, folder, cfg, sheet_path, limit, again, driver)
    message = (f'{report.title}\n리포트 ID: {report.key[1]}\n'
               f'경비 {len(report.rows)}건 · 영수증 첨부 {plan.receipts}건 · 입력 수정 {plan.edits}건\n'
               f'기존 영수증 유지 {plan.skipped_receipts}건 · 매칭 확인 필요 {len(plan.unmatched)}건')
    print(message)
    for task in plan.tasks:
        print(f'  - {task.label}')
    for item in plan.unmatched:
        print(f'  ! 매칭 확인 필요: {item}')
    if not apply:
        print('미리보기입니다. Concur는 변경하지 않았습니다.')
        return 0
    if not plan.tasks:
        return RunResult(1, f'실행할 작업이 없습니다. 매칭하지 못한 거래 {len(plan.unmatched)}건을 확인해 주세요.') if plan.unmatched else RunResult(0, '반영할 작업이 없습니다.')
    if not console.confirm_action(message, '이 리포트에 반영 시작'):
        print('반영을 취소했습니다. Concur는 변경하지 않았습니다.')
        return RunResult(0, '사용자가 반영을 취소했습니다.')
    revalidate(page, report)
    journal = Journal(folder / 'concur-progress.json')
    result = execute(plan.tasks, journal, driver.guard)
    print(f'실행 결과: {journal.path}\n결과 확인 필요 항목은 화면에서 확인해 주세요. 확인된 완료 작업은 재전송하지 않습니다.')
    if result:
        summary = f'실행한 작업 중 저장 결과 확인이 필요한 항목이 있습니다. 매칭하지 못한 거래 {len(plan.unmatched)}건.'
    elif plan.unmatched:
        summary = f'실행 대상 {len(plan.tasks)}개 작업은 모두 반영 확인됐습니다. 매칭하지 못한 거래 {len(plan.unmatched)}건은 처리하지 않았습니다.'
    else:
        summary = f'실행 대상 {len(plan.tasks)}개 작업의 반영을 모두 확인했습니다.'
    print(summary)
    return RunResult(result or int(bool(plan.unmatched)), summary)
