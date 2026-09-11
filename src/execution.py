"""계획 실행과 기록. UI 동작과 검증을 분리하며 불명확한 작업은 재전송하지 않는다."""
from dataclasses import dataclass
from hashlib import sha256
import json
from datetime import datetime, timezone

from .worksheet import write_json


@dataclass
class Task:
    key: str
    label: str
    apply: object
    verify: object


def task_key(report, expense, kind, intent):
    digest = sha256(json.dumps(intent, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return '|'.join([report, expense, kind, digest])


class Journal:
    def __init__(self, path):
        self.path = path
        self.data = {'version': 1, 'tasks': {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding='utf-8'))
            if self.data.get('version') != 1 or not isinstance(self.data.get('tasks'), dict):
                raise ValueError('실행 기록 형식을 읽지 못했습니다.')

    def state(self, key):
        return self.data['tasks'].get(key, {}).get('state')

    def set(self, task, state, message=''):
        self.data['tasks'][task.key] = {'state': state, 'label': task.label, 'message': message,
            'updated': datetime.now(timezone.utc).isoformat()}
        write_json(self.path, self.data)


def execute(tasks, journal, guard):
    failures = 0
    for i, task in enumerate(tasks, 1):
        guard()  # 다른 리포트나 로그아웃 상태에서는 다음 작업도 시작하지 않는다.
        print(f'[{i}/{len(tasks)}] 현재 상태 확인: {task.label}')
        previous = journal.state(task.key)
        try:
            before = task.verify()
        except Exception as exc:
            journal.set(task, previous if previous in ('running', 'needs_review') else 'pending', f'실행 전 조회 실패: {exc}')
            print(f'[{i}/{len(tasks)}] 확인 필요: {task.label} - {exc}')
            failures += 1
            continue
        if before is True:
            journal.set(task, 'verified')
            print(f'[{i}/{len(tasks)}] 이미 반영됨: {task.label}')
            continue
        if previous in ('running', 'needs_review') or before is None:
            journal.set(task, 'needs_review' if previous in ('running', 'needs_review') else 'pending',
                        '이전 작업 또는 현재 상태를 확정할 수 없어 자동 재실행하지 않았습니다.')
            print(f'[{i}/{len(tasks)}] 확인 필요: {task.label} · 자동 재실행하지 않음')
            failures += 1
            continue
        journal.set(task, 'running')  # 기록 실패 시 쓰기 작업 자체를 시작하지 않는다.
        error = ''
        try:
            print(f'[{i}/{len(tasks)}] 반영 중: {task.label}')
            task.apply()
        except Exception as exc:
            error = str(exc)
        try:
            print(f'[{i}/{len(tasks)}] 저장 결과 확인: {task.label}')
            after = task.verify()
        except Exception as exc:
            after, error = None, f'{error} / 결과 조회: {exc}'
        if after is True:
            journal.set(task, 'verified')
            print(f'[{i}/{len(tasks)}] 저장 확인 완료: {task.label}')
        else:
            journal.set(task, 'needs_review', error or '저장 결과가 입력 계획과 일치하는지 확인하지 못했습니다.')
            print(f'[{i}/{len(tasks)}] 결과 확인 필요: {task.label} - {error}')
            failures += 1
    return 1 if failures else 0
