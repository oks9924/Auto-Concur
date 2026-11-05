"""쓰지 않고 '어떤 필드가 필수인가' 를 시험한다.

    python -m src.probe_fields

2026-11-05 실측: 이 서버는 introspection 이 막혀 있다(아는 작전 이름으로 같은 내용을
보내도 같은 400 이라 거부 대상은 이름이 아니라 `__type` 이다). 그래서 스키마에 물을
수 없다.

그런데 알고 싶은 건 하나뿐이다 - **어떤 필드가 필수인가.** 그건 묻지 않아도 알 수
있다. `@skip(if: true)` 로 보내면 **변수 검사는 하고 resolver 는 실행하지 않는다.**
필드를 하나씩 빼서 보내 보면 어느 것이 필수인지 드러난다. 아무것도 쓰이지 않는다.

ID 는 가짜를 쓴다. 검증은 타입과 존재만 보지 그 ID 가 실재하는지 보지 않고, 어차피
실행되지 않는다.

**"통과" 는 그 모양이 검증을 지난다는 뜻이지 업무 규칙까지 맞다는 뜻은 아니다.**
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from . import concur_graphql as gq, console, paths
from .attach_receipts import AttachError, open_report

FAKE = 'PROBE'

SAVE_ATTENDEES = (
    'mutation SaveExpenseAttendees($userId: String!, $contextRole: ContextRoleType!, '
    '$params: SaveAttendeesLegacyParams!, $skip: Boolean! = false) {\n'
    '  employee(userId: $userId, contextRole: $contextRole) {\n'
    '    saveAttendeesLegacy(params: $params) @skip(if: $skip) { status }\n'
    '  }\n'
    '}')

# 명세는 변수가 아니라 필드 인자라서, 인자를 뺀 문서를 따로 만든다.
ROOM_RATE = (
    'mutation CreateSameRoomRateMutation($reportId: String!, $expenseId: String!, '
    '$userId: String!, $contextRole: ContextRoleType!, $roomRateObject: RoomRateAndTax!, '
    '{decl}$skip: Boolean! = false) {{\n'
    '  createSameRoomRateItemizations(reportId: $reportId, expenseId: $expenseId, '
    'userId: $userId, contextRole: $contextRole, roomRateObject: $roomRateObject'
    '{arg}) @skip(if: $skip) {{ status {{ success }} }}\n'
    '}}')

ROOM_RATE_WITH = ROOM_RATE.format(decl='$roomRateExpenseTypeId: String!, ',
                                  arg=', roomRateExpenseTypeId: $roomRateExpenseTypeId')
ROOM_RATE_WITHOUT = ROOM_RATE.format(decl='', arg='')


def attendee(*drop: str) -> dict:
    """실측된 다섯 필드에서 몇 개를 뺀 참석자 하나."""
    one = {'atnKey': FAKE, 'atnTypeCode': 'EMPL', 'versionNumber': 0,
           'instanceCount': 1, 'transactionAmount': 1000}
    for name in drop:
        one.pop(name, None)
    return one


def controls(user_id: str, role: str) -> list[tuple[str, str, str, dict]]:
    """반드시 거부돼야 하는 것들. **이게 없으면 위 결과를 해석할 수 없다.**

    2026-11-05 에 시험 7건이 전부 통과했는데, 그게 "전부 선택" 인지 "검사가 아예
    안 된다" 인지 가릴 수 없었다. 대조군을 안 넣은 내 잘못이다. 일부러 틀린 것을
    같이 보내서, 그게 거부돼야 나머지 통과가 뜻을 갖는다.
    """
    def attendees(one, extra=None):
        params = {'reportId': FAKE, 'entryId': FAKE, 'attendees': [one]}
        return {'userId': user_id, 'contextRole': role, 'params': params, **(extra or {})}

    broken_field = SAVE_ATTENDEES.replace('saveAttendeesLegacy(',
                                          'saveAttendeesLegacyNOPE(')
    one = attendee()
    return [
        ('대조군: 없는 입력 필드를 넣음', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(dict(one, atnKeyNOPE='x'))),
        ('대조군: 없는 mutation 필드를 부름', 'SaveExpenseAttendees', broken_field,
         attendees(one)),
        ('대조군: 숫자 자리에 글자를 넣음', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(dict(one, versionNumber='숫자아님'))),
        ('대조군: 필수 변수를 통째로 뺌', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         {'contextRole': role,
          'params': {'reportId': FAKE, 'entryId': FAKE, 'attendees': [one]}}),
    ]


def probes(user_id: str, role: str) -> list[tuple[str, str, str, dict]]:
    """(무엇을 보는가, 작전 이름, 문서, 변수). 전부 skip=true 로 나간다."""
    def attendees(one):
        return {'userId': user_id, 'contextRole': role,
                'params': {'reportId': FAKE, 'entryId': FAKE, 'attendees': [one]}}

    room = {'reportId': FAKE, 'expenseId': FAKE, 'userId': user_id,
            'contextRole': role, 'roomRateObject': {'roomRate': 1000}}
    return [
        ('참석자: 실측된 다섯 필드 그대로', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(attendee())),
        ('참석자: transactionAmount 빼고', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(attendee('transactionAmount'))),
        ('참석자: instanceCount 빼고', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(attendee('instanceCount'))),
        ('참석자: versionNumber 빼고', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(attendee('versionNumber'))),
        ('참석자: 금액·수량 둘 다 빼고', 'SaveExpenseAttendees', SAVE_ATTENDEES,
         attendees(attendee('transactionAmount', 'instanceCount'))),
        ('숙박 명세: roomRateExpenseTypeId 주고', 'CreateSameRoomRateMutation',
         ROOM_RATE_WITH, dict(room, roomRateExpenseTypeId=FAKE)),
        ('숙박 명세: roomRateExpenseTypeId 빼고', 'CreateSameRoomRateMutation',
         ROOM_RATE_WITHOUT, room),
    ]


def reason(exc) -> str:
    text = str(exc)
    return (text.split('—')[-1] if '—' in text else text).strip()[:200]


def run() -> int:
    pw, ctx, page, report = open_report(automatic=True)
    try:
        user_id, role = gq.identify(ctx, report.key[1])
        print(f'\n리포트 {report.key[1]} 세션으로 모양만 시험합니다.')
        print('모든 시험이 @skip(if: true) 로 나갑니다 — resolver 는 실행되지 않습니다.\n')

        def ask(group):
            results = []
            for what, name, query, variables in group:
                assert '@skip(if: $skip)' in query, '시험 문서에 @skip 이 없습니다'
                try:
                    gq.call(ctx, name, query, dict(variables, skip=True))
                except gq.GraphQLError as exc:
                    print(f'  거부  {what}')
                    print(f'          {reason(exc)}')
                    results.append({'probe': what, 'passed': False, 'reason': reason(exc)})
                else:
                    print(f'  통과  {what}')
                    results.append({'probe': what, 'passed': True})
            return results

        # 대조군 먼저. 이게 거부되지 않으면 나머지는 볼 필요도 없다.
        print('-- 대조군 (전부 거부돼야 합니다) --')
        checks = ask(controls(user_id, role))
        valid = all(not r['passed'] for r in checks)
        print()
        print('-- 시험 --')
        found = ask(probes(user_id, role))

        print()
        if not valid:
            slipped = [r['probe'] for r in checks if r['passed']]
            print('!! 대조군이 통과했습니다: ' + ', '.join(slipped))
            print('!! 이 서버는 @skip 이 붙은 호출을 검사하지 않습니다.')
            print('!! 따라서 위 "시험" 결과는 아무 뜻이 없습니다 — 필수 여부를 모릅니다.')
        else:
            print('대조군이 전부 거부됐으므로 위 결과는 뜻이 있습니다.')
            print('"통과" 는 그 모양이 검증을 지난다는 뜻이지 업무 규칙까지 맞다는 뜻은 아닙니다.')
        found = {'controlsValid': valid, 'controls': checks, 'probes': found}
        out = paths.at('inspect-out')
        out.mkdir(parents=True, exist_ok=True)
        target = out / f'probe-{datetime.now():%Y%m%d-%H%M%S}.json'
        target.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'기록: {target}\n아무것도 바꾸지 않았습니다.')
        return 0
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    argparse.ArgumentParser(description='필수 필드를 쓰지 않고 시험합니다').parse_args()
    try:
        return run()
    except (AttachError, gq.GraphQLError) as exc:
        print(f'\n시험을 마치지 못했습니다: {exc}\n아무것도 바꾸지 않았습니다.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
