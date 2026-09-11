from unittest.mock import Mock
import pytest
from src.execution import Journal, Task, execute, task_key


def test_success_requires_readback_and_is_not_reapplied(tmp_path):
    journal = Journal(tmp_path / 'journal.json')
    operation = Mock()
    task = Task('1', 'test', operation, Mock(side_effect=[False, True, True]))
    assert execute([task], journal, Mock()) == 0
    assert execute([task], journal, Mock()) == 0
    operation.assert_called_once()
    assert Journal(journal.path).state('1') == 'verified'


def test_timeout_after_success_is_verified_without_second_click(tmp_path):
    operation = Mock(side_effect=RuntimeError('timeout'))
    task = Task('1', 'test', operation, Mock(side_effect=[False, True]))
    assert execute([task], Journal(tmp_path / 'j.json'), Mock()) == 0
    operation.assert_called_once()


def test_uncertain_write_blocks_replay_but_can_later_be_verified(tmp_path):
    journal = Journal(tmp_path / 'j.json')
    task = Task('1', 'test', Mock(), Mock(side_effect=[False, None, False, True]))
    assert execute([task], journal, Mock()) == 1
    assert journal.state('1') == 'needs_review'
    assert execute([task], journal, Mock()) == 1
    assert execute([task], journal, Mock()) == 0
    task.apply.assert_called_once()


def test_process_interruption_leaves_running_record(tmp_path):
    journal = Journal(tmp_path / 'j.json')
    task = Task('1', 'test', Mock(side_effect=KeyboardInterrupt), lambda: False)
    with pytest.raises(KeyboardInterrupt):
        execute([task], journal, Mock())
    task.apply.reset_mock()
    assert execute([task], Journal(journal.path), Mock()) == 1
    task.apply.assert_not_called()


def test_read_failure_before_write_can_retry(tmp_path):
    journal = Journal(tmp_path / 'j.json')
    task = Task('1', 'test', Mock(), Mock(side_effect=[RuntimeError('network'), False, True]))
    assert execute([task], journal, Mock()) == 1
    assert journal.state('1') == 'pending'
    assert execute([task], journal, Mock()) == 0
    task.apply.assert_called_once()


def test_wrong_report_and_record_failure_stop_before_write(tmp_path, monkeypatch):
    journal = Journal(tmp_path / 'j.json')
    task = Task('1', 'test', Mock(), lambda: False)
    with pytest.raises(ValueError):
        execute([task], journal, Mock(side_effect=ValueError('wrong report')))
    monkeypatch.setattr(journal, 'set', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        execute([task], journal, Mock())
    task.apply.assert_not_called()


def test_identity_includes_report_and_input():
    assert task_key('R1', 'E1', 'edit', 'a') != task_key('R2', 'E1', 'edit', 'a')
    assert task_key('R1', 'E1', 'edit', 'a') != task_key('R1', 'E1', 'edit', 'b')
