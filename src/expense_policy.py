"""입력 안내와 실행이 함께 쓰는 유형별 업무 규칙."""
ATTENDEE_REQUIRED_TYPE = '내부 직원간 식음료'
LODGING_TYPE = '숙박비'
TRANSIT_TYPE = '대중교통비(지하철, 버스, 기차, 택시, 통행료 등)'
GREEN_BY_TYPE = {
    ATTENDEE_REQUIRED_TYPE: ['참석자', '추가 참석자', '비즈니스목적', '코멘트'],
    LODGING_TYPE: ['입실날짜', '퇴실날짜', '숙박위치', 'Booking Channel', '코멘트'],
    TRANSIT_TYPE: ['코멘트'],
}


def description_only(label, code=None):
    return code == 'TRAIN' or (label or '').strip().startswith('대중교통')


# A registered input guide is NOT proof that the company's Concur form requires a field.
# Keep unreviewed expense types unmarked rather than inferring requirements by name.
_GUIDE_BY_CODE = {'01182': ATTENDEE_REQUIRED_TYPE, 'LODNG': LODGING_TYPE, 'TRAIN': TRANSIT_TYPE}


def input_guide(label, code=None):
    name = (label or '').strip()
    canonical = name if name in GREEN_BY_TYPE else _GUIDE_BY_CODE.get(code)
    return list(GREEN_BY_TYPE.get(canonical, [])), canonical is not None
