import pytest
from src import replacements as rp


def test_literal_replace_protects_identity_and_hidden_rows():
    columns = ['금액', '코멘트', '비즈니스목적']
    data = [['A.B', 'A.B A.B', 'a.b'], ['100', 'A.B', '']]
    pattern, cells = rp.matches(data, columns, [0], 'A.B')
    result = rp.replaced(data, pattern, cells, r'\1')
    assert result == [['A.B', r'\1 \1', r'\1'], ['100', 'A.B', '']]
    assert data[0][1] == 'A.B A.B'


def test_field_case_whole_and_empty_replacement():
    data = [['Foo', 'Foo bar'], ['foo', 'foo']]
    pattern, cells = rp.matches(data, ['코멘트', '비즈니스목적'], [0, 1],
                               'Foo', '코멘트', True, True)
    assert cells == [(0, 0)]
    assert rp.replaced(data, pattern, cells, '')[0][0] == ''
    with pytest.raises(ValueError):
        rp.matches(data, ['코멘트'], [0], '')
