"""우리가 Concur에 **보낼 수 있는 것**의 울타리.

사용자 질문(2026-11-05): "GraphQL로 쏴도 경비 저장만 하는 거지 제출까진 안 하지?"
맞다. 그런데 "지금은 맞다" 와 "앞으로도 맞다" 는 다르다. 작전을 하나 더 붙이기가
너무 쉬워서, 울타리를 코드가 아니라 테스트로 고정한다.

기록에는 `SaveNewExpenseEntry`·`DeleteExpenseEntries` 가 있지만 그건 사람이 화면에서
손으로 한 것이고, 우리 코드는 부르지 않는다.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'src'

# 우리가 부르는 전부. 늘릴 때는 이 목록부터 고쳐야 한다.
ALLOWED = {
    'GetReportIdAndKey',               # 읽기: 세션 확인
    'GetExistingExpenseEntries',       # 읽기: 경비 목록
    'UpdateExistingExpenseEntry',      # 쓰기: 기존 경비의 필드
    'AttachImage',                     # 쓰기: 기존 경비에 영수증
    'CreateSameRoomRateMutation',      # 쓰기: 기존 경비의 명세
    'CreateDifferentRoomRateMutation',
    'GetAttendeesByPredictiveSearch',  # 읽기: 참석자 후보
    'SaveExpenseAttendees',            # 쓰기: 기존 경비의 참석자
}

# 돌이킬 수 없거나 승인 흐름을 움직이는 것들. 실수로도 들어오면 안 된다.
FORBIDDEN = ('submitreport', 'submitexpensereport', 'deleteexpense', 'deletereport',
             'createexpense', 'savenewexpense', 'approvereport', 'recallreport',
             'workflowaction')


def operations(text):
    """GraphQL 상수 안에서만 찾는다. 한글 주석의 '이 mutation 은...' 이 잡혔었다."""
    quote = chr(39) * 3
    found = set()
    for block in re.findall(r'^[A-Z][A-Z_]* = ' + quote + r'(.*?)' + quote, text, re.S | re.M):
        found |= set(re.findall(r'(?:query|mutation)\s+([A-Za-z]\w*)', block))
    return found


def test_부를_수_있는_작전이_이것뿐이다():
    found = operations((SRC / 'concur_graphql.py').read_text(encoding='utf-8'))
    assert found == ALLOWED, f'늘었거나 줄었습니다: {found ^ ALLOWED}'


def test_제출_삭제_경비생성은_어디에도_없다():
    """진단 도구(record_calls, probe_fields)는 뺀다 - 기록하거나 시험만 한다."""
    skip = {'record_calls.py', 'probe_fields.py', 'inspect_schema.py'}
    for path in SRC.glob('*.py'):
        if path.name in skip:
            continue
        low = path.read_text(encoding='utf-8').lower()
        for word in FORBIDDEN:
            assert word not in low, f'{path.name} 에 {word} 가 있습니다'


def test_시험_도구는_mutation_을_실행하지_않는다():
    """probe_fields 는 mutation 문서를 보내지만 전부 @skip(if: true) 다."""
    text = (SRC / 'probe_fields.py').read_text(encoding='utf-8')
    for block in re.findall(r"'''(.*?)'''", text, re.S):
        if 'mutation' in block:
            assert '@skip(if: $skip)' in block, block[:120]


def test_쓰기는_전부_사전검증을_거친다():
    """보내기 전에 @skip(if: true) 로 한 번 검증한다. 그 호출이 빠진 쓰기가 없어야 한다."""
    text = (SRC / 'concur_graphql.py').read_text(encoding='utf-8')
    writes = re.findall(r'_write\(page\.context, [^)]*dry=(True|False)\)', text)
    assert writes.count('True') == writes.count('False') == 4, writes
    assert len(writes) == 8, '쓰기마다 검증이 하나씩 붙어야 합니다'
