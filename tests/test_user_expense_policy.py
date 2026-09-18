"""사용자가 지정한 7종 안내와 기존 규칙 보존. 실제 Concur 접속 없음."""
import pytest
from src import expense_policy as p


@pytest.mark.parametrize('kind', p.DESCRIPTION_ONLY_TYPES)
def test_description_types_have_only_comment_guide(kind):
    assert p.description_only(kind)
    assert p.input_guide(kind) == (['코멘트'], True)
    assert p.GREEN_BY_TYPE[kind] == ['코멘트']


@pytest.mark.parametrize('code', ['TRAIN', 'AIRFR', 'PARKG', '01143'])
def test_preexisting_codes_match_even_when_label_differs(code):
    assert p.description_only('account-specific label', code)
    assert p.input_guide('account-specific label', code) == (['코멘트'], True)


@pytest.mark.parametrize('label,code,fields', [
    (p.ATTENDEE_REQUIRED_TYPE, '01182', ['참석자', '추가 참석자', '비즈니스목적', '코멘트']),
    (p.LODGING_TYPE, 'LODNG', ['입실날짜', '퇴실날짜', '숙박위치', 'Booking Channel', '코멘트']),
])
def test_meal_and_lodging_keep_existing_behavior(label, code, fields):
    assert p.input_guide(label) == (fields, True)
    assert p.input_guide('account-specific label', code) == (fields, True)
    assert not p.description_only(label, code)


@pytest.mark.parametrize('label,code', [
    ('렌터카비', 'CARRT'), ('일반 배송비(택배, 퀵서비스 등)', '01005'),
    ('법인폰 관련 비용', 'CELPH'), ('내부 직원 용 선물', 'GIFTS'),
    ('외부인 포함 식음료 (법인카드 결제건, 또는 3만원 이하)', '01093'),
    ('외부인 포함 식음료 (3만원 초과 현금 및 개인카드)', '01094'),
    ('내부 직원간 행사비 (워크샵 등)', '01004'),
    ('환급 불가 경비/개인 지출', '01000'), ('다른 교육비', 'UNKNOWN'),
    ('', None), (None, None),
])
def test_other_types_are_not_guessed_or_changed(label, code):
    assert not p.description_only(label, code)
    assert p.input_guide(label, code) == ([], False)


def test_training_preserves_user_spelling_without_inventing_code():
    assert p.TRAINING_TYPE == '업무 및 영량 향상관련 교육비'
    assert p.TRAINING_TYPE not in p._GUIDE_BY_CODE.values()
    assert p.TRAINING_TYPE not in p._DESCRIPTION_BY_CODE.values()


def test_each_guide_is_independent_and_return_is_copy():
    first, _ = p.input_guide(p.PARKING_TYPE)
    first.append('참석자')
    assert p.input_guide(p.PARKING_TYPE) == (['코멘트'], True)
    assert len({id(p.GREEN_BY_TYPE[t]) for t in p.DESCRIPTION_ONLY_TYPES}) == 5


def test_old_transit_shorthand_and_outer_whitespace_are_retained():
    assert p.description_only(' 대중교통비 ')
    assert p.description_only(' 항공비 ')
    assert p.input_guide(' 주차비 ') == (['코멘트'], True)
