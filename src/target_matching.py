"""대상별 보수적 매칭. 불완전한 경비도 경쟁 후보에서 조용히 제거하지 않는다.

화면 전체의 관측 안정성과 개별 거래의 매칭 가능성은 별도 판단이다.
읽힌 날짜 또는 금액이 불일치하면 무관, 모르는 값은 와일드카드다.
수동 생성 여부/오류 아이콘/영수증 유무는 제외 근거로 사용하지 않는다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from . import hangul

VENDOR_MIN = 0.6
VENDOR_MARGIN = 0.15


@dataclass(frozen=True)
class Decision:
    row: Any = None
    how: str = ''
    reason: str = ''


def may_match(entry, row, tolerance: int) -> bool:
    """확실히 읽힌 값 하나만 달라도 이 거래의 후보는 아니다."""
    if row.amount is not None and row.amount != entry.amount:
        return False
    if row.when is not None and abs((row.when - entry.when).days) > tolerance:
        return False
    return True


def row_issues(row, id_counts) -> list[str]:
    issues = []
    if not row.expense_id:
        issues.append('경비 ID 누락')
    elif id_counts[row.expense_id] > 1:
        issues.append('경비 ID 중복')
    if row.when is None:
        issues.append('날짜 미확인')
    if row.amount is None:
        issues.append('금액 미확인')
    return issues


def decide(entries, rows, tolerance: int) -> list[Decision]:
    """순서 독립적인 1:1 매칭. 애매한 후보를 다른 거래가 선점하지 않는다."""
    if tolerance < 0:
        raise ValueError('날짜 허용 오차는 0 이상이어야 합니다.')
    if any(getattr(r, 'read_problem', '') for r in rows):
        return [Decision(reason='보류: 목록 로딩/전체 행 수 확인 필요') for _ in entries]
    counts = Counter(r.expense_id for r in rows if r.expense_id)
    options, reasons, methods = [], [], []
    for entry in entries:
        candidates = [i for i, row in enumerate(rows) if may_match(entry, row, tolerance)]
        issues = sorted({issue for i in candidates for issue in row_issues(rows[i], counts)})
        reason, how = '', '단독'
        if not candidates:
            reason = '후보 없음'
        elif issues:
            reason = '보류: 관련 가능 경비의 ' + ', '.join(issues)
        elif len(candidates) > 1:
            # 기존 가맹점 판별 기준은 유지하되 순서 배정으로 떨어지지 않는다.
            scored = sorted(((hangul.similarity(entry.merchant, rows[i].vendor), i)
                             for i in candidates), reverse=True)
            best, second = scored[:2]
            if best[0] >= VENDOR_MIN and best[0] - second[0] >= VENDOR_MARGIN:
                candidates, how = [best[1]], '가맹점'
            else:
                reason = '보류: 날짜·금액이 같은 후보를 구별할 수 없음'
        options.append(candidates)
        reasons.append(reason)
        methods.append(how)

    # 보류된 거래의 후보도 예약한다. 앞 거래부터 하나씩 소비하면 결과가 바뀐다.
    owners = Counter(i for candidates in options for i in candidates)
    result = []
    for candidates, reason, how in zip(options, reasons, methods):
        if reason:
            result.append(Decision(reason=reason))
        elif owners[candidates[0]] > 1:
            result.append(Decision(reason='보류: 같은 경비가 여러 원본 거래의 후보임'))
        else:
            result.append(Decision(rows[candidates[0]], how))
    return result


def match(entries, rows, tolerance: int):
    decisions = decide(entries, rows, tolerance)
    pairs = [(entry, d.row, d.how) for entry, d in zip(entries, decisions) if d.row is not None]
    missed = [(entry, d.reason) for entry, d in zip(entries, decisions) if d.row is None]
    return pairs, missed


@dataclass
class MatchBatch:
    targets: list
    bindings: dict
    receipt_pairs: list
    receipt_missing: list
    edit_pairs: list
    edit_missing: list
    excluded_rows: list
    tolerance: int

    def verify(self, intended, current_rows) -> bool:
        """새 목록에서도 같은 원본이 같은 경비를 유일하게 가리키는지 검사한다."""
        target_index = self.bindings.get(intended.expense_id)
        if target_index is None:
            return False
        current = decide(self.targets, current_rows, self.tolerance)[target_index].row
        return bool(current is not None and current.expense_id == intended.expense_id
                    and current.when == intended.when and current.amount == intended.amount)


def match_sources(slips, entries, rows, tolerance: int) -> MatchBatch:
    """영수증과 작업지에 같은 거래가 있으면 하나의 대상으로 함께 매칭한다.

    승인번호·날짜·금액·가맹점이 같고 각 소스에 정확히 하나씩 있을 때만 합친다.
    승인번호가 없거나 같은 소스 안에 중복이 있으면 별개 경쟁 대상으로 보류한다.
    """
    groups = defaultdict(list)
    sources = {'receipt': list(slips), 'edit': list(entries)}
    for stage, values in sources.items():
        for index, entry in enumerate(values):
            approval = str(entry.approval or '').strip()
            key = ('known', approval, entry.when, entry.amount, (entry.merchant or '').strip()) if approval else ('unknown', stage, index)
            groups[key].append((stage, index, entry))
    targets, positions = [], {}
    for group in groups.values():
        unique_stages = len({stage for stage, _, _ in group}) == len(group)
        subgroups = [group] if unique_stages else [[item] for item in group]
        for members in subgroups:
            target_index = len(targets)
            targets.append(members[0][2])
            for stage, index, _ in members:
                positions[stage, index] = target_index
    decisions = decide(targets, rows, tolerance)
    paired, missed = {}, {}
    for stage, values in sources.items():
        paired[stage], missed[stage] = [], []
        for index, entry in enumerate(values):
            decision = decisions[positions[stage, index]]
            if decision.row is None:
                missed[stage].append((entry, decision.reason))
            else:
                paired[stage].append((entry, decision.row, decision.how))
    bindings = {d.row.expense_id: i for i, d in enumerate(decisions) if d.row is not None}
    excluded = [row for row in rows if not any(may_match(entry, row, tolerance) for entry in targets)]
    return MatchBatch(targets, bindings, paired['receipt'], missed['receipt'],
                      paired['edit'], missed['edit'], excluded, tolerance)
