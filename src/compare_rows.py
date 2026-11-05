"""경비 목록을 화면과 GraphQL로 둘 다 읽어 비교한다. 아무것도 바꾸지 않는다.

    python -m src.compare_rows

읽기 이관의 1단계다. 지금은 **옮기지 않고 재기만 한다.** 둘이 한동안 일치하는 것을
본 뒤에 코드가 GraphQL 쪽을 쓰게 하고, 그때도 DOM 경로는 폴백으로 남긴다.

어긋나면 **DOM을 믿는다.** 지금 돌고 있는 것이 DOM 쪽이고, 새로 붙인 쪽이 틀렸을
가능성이 더 크다. 그래서 이 도구는 판정하지 않고 차이를 적는다.

주문도 저장도 첨부도 하지 않는다. GraphQL 호출은 질의(query)뿐이고 mutation 은
부르지 않는다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime

from . import concur_graphql as gq, console, paths
from .attach_receipts import AttachError, open_report

OUT_DIR = paths.at('inspect-out')


def differences(dom_rows, api_rows) -> list[dict]:
    """경비 ID로 짝지어 날짜·금액·영수증 유무를 비교한다."""
    by_dom = {r.expense_id: r for r in dom_rows if r.expense_id}
    by_api = {e.expense_id: e for e in api_rows if e.expense_id}
    found = []
    for expense_id in sorted(set(by_dom) | set(by_api)):
        dom, api = by_dom.get(expense_id), by_api.get(expense_id)
        if dom is None or api is None:
            found.append({'expenseId': expense_id,
                          'what': '화면에만 있음' if api is None else 'GraphQL에만 있음'})
            continue
        for what, left, right in (
                ('날짜', dom.when, api.when),
                ('금액', dom.amount, api.amount),
                ('영수증', dom.has_receipt, api.has_receipt)):
            if left != right:
                found.append({'expenseId': expense_id, 'what': what,
                              '화면': str(left), 'GraphQL': str(right),
                              '화면원문': dom.raw_date if what == '날짜' else dom.raw_amount,
                              'GraphQL원문': api.raw_date if what == '날짜' else api.raw_amount,
                              'GraphQL메모': api.note})
    return found


def report_lines(dom_rows, api_rows, children: int) -> list[str]:
    lines = [f'화면 {len(dom_rows)}행 · GraphQL {len(api_rows)}행'
             + (f' (명세 자식 {children}행은 제외)' if children else '')]
    unread_dom = sum(1 for r in dom_rows if r.when is None or r.amount is None)
    unread_api = sum(1 for e in api_rows if e.when is None or e.amount is None)
    lines.append(f'  못 읽은 날짜·금액: 화면 {unread_dom}행 · GraphQL {unread_api}행')
    notes = sorted({e.note for e in api_rows if e.note})
    if notes:
        lines.append('  GraphQL 메모: ' + ' / '.join(notes))
    return lines


def run() -> int:
    pw, ctx, page, report = open_report(automatic=True)
    try:
        report_id = report.key[1]
        print(f'\n리포트: {report.title}\n  ID: {report_id}')

        dom_rows = list(report.rows)
        print(f'  화면에서 {len(dom_rows)}행을 읽었습니다.')

        print('  GraphQL로 같은 목록을 읽습니다 (읽기만 합니다)...')
        user_id, role = gq.identify(ctx, report_id)
        print(f'  세션 확인: contextRole={role}')
        every = gq.entries(ctx, report_id, user_id, role)
        api_rows = gq.top_level(every)
        children = len(every) - len(api_rows)

        for line in report_lines(dom_rows, api_rows, children):
            print(line)

        gaps = differences(dom_rows, api_rows)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / f'compare-{datetime.now():%Y%m%d-%H%M%S}.json'
        target.write_text(json.dumps({
            'reportId': report_id,
            'counts': {'dom': len(dom_rows), 'graphql': len(api_rows), 'children': children},
            'differences': gaps,
            'graphql': [asdict(e) | {'when': str(e.when)} for e in api_rows],
        }, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

        if not gaps:
            print('\n두 경로가 일치합니다. 차이 0건.')
        else:
            print(f'\n차이 {len(gaps)}건 — 화면 쪽을 믿습니다. 코드는 바뀌지 않았습니다.')
            for gap in gaps[:12]:
                print('  ' + json.dumps(gap, ensure_ascii=False))
            if len(gaps) > 12:
                print(f'  ... 나머지 {len(gaps) - 12}건은 파일에')
        print(f'\n기록: {target}')
        print('아무것도 바꾸지 않았습니다.')
        return 0
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    argparse.ArgumentParser(description='경비 목록을 화면과 GraphQL로 비교합니다 (변경 없음)').parse_args()
    try:
        return run()
    except (AttachError, gq.GraphQLError) as exc:
        print(f'\n비교를 마치지 못했습니다: {exc}')
        print('아무것도 바꾸지 않았습니다.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
