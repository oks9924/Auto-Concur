"""입력 안내와 실행이 함께 쓰는 유형별 업무 규칙."""
ATTENDEE_REQUIRED_TYPE = '내부 직원간 식음료'
LODGING_TYPE = '숙박비'
TRANSIT_TYPE = '대중교통비(지하철, 버스, 기차, 택시, 통행료 등)'
# 사용자 제공 표기를 보존한다. 실제 Concur 표시명/코드는 아직 미확인이다.
# 교육비 코드를 추측해서 settings.EXPENSE_TYPE_CODES에 추가하지 않는다.
TRAINING_TYPE = '업무 및 영량 향상관련 교육비'
AIRFARE_TYPE = '항공비'
PARKING_TYPE = '주차비'
SUPPLIES_TYPE = '기타 사무용품(문구류 등)'
DESCRIPTION_ONLY_TYPES = (
    TRANSIT_TYPE, TRAINING_TYPE, AIRFARE_TYPE, PARKING_TYPE, SUPPLIES_TYPE,
)
# 아래 코드는 기존 settings.py에 등록되어 있던 값만 사용한다.
_DESCRIPTION_BY_CODE = {
    'TRAIN': TRANSIT_TYPE, 'AIRFR': AIRFARE_TYPE,
    'PARKG': PARKING_TYPE, '01143': SUPPLIES_TYPE,
}
GREEN_BY_TYPE = {
    ATTENDEE_REQUIRED_TYPE: ['참석자', '추가 참석자', '비즈니스목적', '코멘트'],
    LODGING_TYPE: ['입실날짜', '퇴실날짜', '숙박위치', 'Booking Channel', '코멘트'],
    **{name: ['코멘트'] for name in DESCRIPTION_ONLY_TYPES},
}


def description_only(label, code=None):
    """사용자가 지정한 유형은 작업지 코멘트를 설명으로만 반영한다.

    화면 표시용 안내와 Plan의 동일 규칙을 사용한다. 기존 입력/빈칸을
    저장 데이터에서 지우거나 필수값으로 강제하는 함수가 아니다.
    """
    name = (label or '').strip()
    return code in _DESCRIPTION_BY_CODE or name in DESCRIPTION_ONLY_TYPES or name.startswith('대중교통')


# 사용자 업무 입력 안내이며 회사 Concur의 필수 속성을 인증하지 않는다.
_GUIDE_BY_CODE = {
    '01182': ATTENDEE_REQUIRED_TYPE, 'LODNG': LODGING_TYPE,
    **_DESCRIPTION_BY_CODE,
}


def input_guide(label, code=None):
    name = (label or '').strip()
    canonical = name if name in GREEN_BY_TYPE else _GUIDE_BY_CODE.get(code)
    return list(GREEN_BY_TYPE.get(canonical, [])), canonical is not None
