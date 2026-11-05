"""Concur GraphQL 스키마에 직접 물어본다. 읽기만 한다.

    python -m src.inspect_schema
    python -m src.inspect_schema SaveAttendeesLegacyParams RoomRateAndTax

지금까지는 '화면이 보낸 것'을 보고 모양을 짐작했다. 그래서 어떤 필드가 **필수인지,
비워도 되는지**는 알 수 없었다 - 참석자별 금액이나 명세의 요금 입력이 그렇다.
introspection 이 열려 있으면 서버가 그걸 직접 알려준다.

열려 있지 않으면 한 번 돌려서 그걸 알게 된다. 어느 쪽이든 쓰지 않는다 -
질의(query)만 보내고 mutation 은 부르지 않는다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from . import browser, concur_graphql as gq, console, paths
from .attach_receipts import AttachError, open_report

# 우리가 쓰거나 쓸 입력·출력 타입. 인자를 안 주면 이 목록을 본다.
INTERESTING = [
    'UpdateExpenseFieldsInput',      # 저장: 어떤 필드를 보낼 수 있나
    'SaveAttendeesLegacyParams',     # 참석자: 금액·versionNumber 가 필수인가
    'CompactAttendeeInput',
    'RoomRateAndTax',                # 숙박 명세: roomRate 말고 또 필요한가
    'ContextRoleType',
]

TYPE_QUERY = '''query AutoConcurType($name: String!) {
  __type(name: $name) {
    name
    kind
    description
    enumValues { name }
    inputFields {
      name
      defaultValue
      type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
    }
  }
}'''

# createSameRoomRateItemizations 가 받는 인자 이름과 필수 여부.
FIELD_ARGS_QUERY = '''query AutoConcurArgs($name: String!) {
  __type(name: $name) {
    name
    fields {
      name
      args { name type { kind name ofType { kind name ofType { kind name } } } }
    }
  }
}'''


def render(node) -> str:
    """NON_NULL/LIST 포장을 사람이 읽는 꼴로 편다."""
    if not node:
        return '?'
    kind, name = node.get('kind'), node.get('name')
    inner = node.get('ofType')
    if kind == 'NON_NULL':
        return render(inner) + '!'
    if kind == 'LIST':
        return '[' + render(inner) + ']'
    return name or '?'


def describe(found: dict) -> list[str]:
    lines = [f"\n=== {found.get('name')} ({found.get('kind')}) ==="]
    if found.get('description'):
        lines.append('  ' + str(found['description'])[:200])
    for value in found.get('enumValues') or []:
        lines.append(f"  · {value['name']}")
    for field in found.get('inputFields') or []:
        shown = render(field.get('type'))
        need = '필수' if shown.endswith('!') and field.get('defaultValue') is None else '선택'
        default = f"  기본값={field['defaultValue']}" if field.get('defaultValue') is not None else ''
        lines.append(f"  {field['name']:34} {shown:34} {need}{default}")
    for field in found.get('fields') or []:
        if not field.get('args'):
            continue
        lines.append(f"  {field['name']}(")
        for arg in field['args']:
            lines.append(f"      {arg['name']:30} {render(arg.get('type'))}")
        lines.append('  )')
    return lines


# 400 의 원인이 둘로 갈린다. 둘은 결론이 완전히 다르다.
#
#  (가) introspection 이 꺼져 있다 -> 이 경로로는 더 알 수 없다. 다른 방법을 찾는다.
#  (나) 서버가 '아는 작전 이름' 만 받는다 -> 이름을 바꾸면 된다. 그리고 그게
#       사실이면 우리 쓰기 경로에도 함의가 있다(우리는 실제 작전 이름을 쓴다).
#
# 가르는 법: 같은 introspection 질의를 Concur 가 실제로 쓰는 작전 이름으로 한 번 더
# 보낸다. 그래도 400 이면 (가), 통과하면 (나) 다.
KNOWN_NAME = 'GetSiteSettings'


def ask(ctx, name: str, query: str, variables: dict):
    """먼저 우리 이름으로, 안 되면 Concur 가 아는 이름으로. (성공여부, 데이터, 사유)"""
    try:
        return True, gq.call(ctx, name, query, variables), ''
    except gq.GraphQLError as first:
        try:
            data = gq.call(ctx, KNOWN_NAME, query, variables)
        except gq.GraphQLError:
            return False, None, str(first)
        print(f'  ** 작전 이름을 {KNOWN_NAME} 로 바꾸니 통과했습니다 — '
              '서버가 아는 이름만 받습니다 **')
        return True, data, ''


def run(names: list[str]) -> int:
    pw, ctx, page, report = open_report(automatic=True)
    try:
        report_id = report.key[1]
        gq.identify(ctx, report_id)   # 세션이 살아 있는지부터 본다
        print(f'\n리포트 {report_id} 세션으로 스키마를 묻습니다 (읽기만 합니다)\n')

        lines, raw = [], {}
        for name in names:
            ok, data, why = ask(ctx, 'AutoConcurType', TYPE_QUERY, {'name': name})
            if not ok:
                print(f'  {name}: {why}')
                if 'introspection' in why.lower():
                    print('\nintrospection 이 꺼져 있습니다. 이 경로로는 더 알 수 없습니다.')
                    print('남은 방법: 화면에서 해당 동작을 한 번 하고 record_calls 로')
                    print('실제로 보낸 변수를 보는 것입니다(값이 아니라 어떤 키가 갔는지).')
                    return 1
                continue
            found = data.get('__type')
            if not found:
                print(f'  {name}: 그런 타입이 없습니다 (이름이 다를 수 있습니다)')
                continue
            raw[name] = found
            lines.extend(describe(found))

        # 명세 mutation 이 받는 인자도 본다.
        ok, data, why = ask(ctx, 'AutoConcurArgs', FIELD_ARGS_QUERY, {'name': 'Mutation'})
        if ok:
            found = (data.get('__type') or {})
            wanted = {'createSameRoomRateItemizations', 'updateExpense'}
            picked = {'name': 'Mutation', 'kind': 'OBJECT',
                      'fields': [f for f in (found.get('fields') or []) if f.get('name') in wanted]}
            if picked['fields']:
                raw['Mutation'] = picked
                lines.extend(describe(picked))
        else:
            print(f'  Mutation: {why}')

        print('\n'.join(lines) or '  (아무것도 읽지 못했습니다)')
        out = paths.at('inspect-out')
        out.mkdir(parents=True, exist_ok=True)
        target = out / f'schema-{datetime.now():%Y%m%d-%H%M%S}.json'
        target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n기록: {target}\n아무것도 바꾸지 않았습니다.')
        return 0
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description='GraphQL 스키마를 조회합니다 (변경 없음)')
    ap.add_argument('names', nargs='*', help='볼 타입 이름입니다. 없으면 기본 목록을 봅니다')
    args = ap.parse_args()
    try:
        return run(args.names or INTERESTING)
    except (AttachError, gq.GraphQLError) as exc:
        print(f'\n조회를 마치지 못했습니다: {exc}\n아무것도 바꾸지 않았습니다.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
