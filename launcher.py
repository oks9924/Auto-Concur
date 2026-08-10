"""exe로 묶을 때의 진입점.

`python -m src.gui` 는 인수가 필요하다. 그런데 실행 파일만 등록할 수 있고
인수는 못 주는 환경이 있다(DuoNX). 그래서 인수 없이 눌러도 창이 뜨는 진입점을
따로 둔다. PyInstaller가 이 파일을 Auto-Concur.exe 로 묶는다.

여기서 상대 임포트를 못 쓴다 - 이 파일은 패키지 밖이다.
"""

from src.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
