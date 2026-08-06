"""한글 가맹점명을 Concur의 로마자 표기와 견주기 위한 것.

manifest의 가맹점명은 한글이고(`라한호텔울산`) Concur는 로마자다
(`RA HAN HO TEL UL SAN`). 같은 가게인지 보려면 한쪽을 옮겨야 한다.

표기가 딱 떨어지지 않는다. 로마자 표기법으로 `쿠우`는 `ku u`인데 Concur는
`KU WOO`로 적는다. 그래서 완전 일치가 아니라 유사도로 본다. 확실할 때만
쓰고 애매하면 판단하지 않는다.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

BASE = 0xAC00
INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj",
            "ch", "k", "t", "p", "h"]
MEDIALS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe",
           "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
FINALS = ["", "k", "k", "ks", "n", "nj", "nh", "t", "l", "lk", "lm", "lp", "ls",
          "lt", "lp", "lh", "m", "p", "ps", "t", "t", "ng", "t", "t", "k", "t",
          "p", "h"]

NON_ALNUM = re.compile(r"[^a-z0-9]")


def romanize(text: str) -> str:
    """한글 음절을 로마자로 옮긴다. 한글이 아닌 글자는 그대로 둔다."""
    out = []
    for ch in text:
        code = ord(ch) - BASE
        if 0 <= code < 11172:
            initial, rest = divmod(code, 588)
            medial, final = divmod(rest, 28)
            out.append(INITIALS[initial] + MEDIALS[medial] + FINALS[final])
        else:
            out.append(ch)
    return "".join(out)


def _normalize(text: str) -> str:
    """비교용으로 다듬는다. 표기 흔들림을 몇 개 흡수한다."""
    s = NON_ALNUM.sub("", text.lower())
    # Concur는 '우'를 woo/oo로 적고 로마자 표기법은 u다.
    s = s.replace("woo", "u").replace("oo", "u")
    return s


def similarity(korean: str, latin: str) -> float:
    """한글 이름과 로마자 이름이 같은 가게일 가능성. 0~1."""
    if not korean or not latin:
        return 0.0
    return SequenceMatcher(None, _normalize(romanize(korean)), _normalize(latin)).ratio()
