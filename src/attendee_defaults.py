"""Fill a blank attendee only on an explicit transition to an internal meal.

This is an editor-side convenience, never a load/save or Concur-side default.
The tksheet adapter is regression-tested against the pinned tksheet==7.6.0.
"""
from contextlib import contextmanager
from .expense_policy import ATTENDEE_REQUIRED_TYPE


def _text(value):
    return str(value or '').strip()


def _internal(kind, cfg):
    name = _text(kind)
    return name == ATTENDEE_REQUIRED_TYPE or cfg.get('expense_type_codes', {}).get(name) == '01182'


def attendee_on_type_change(before: dict, after: dict, cfg: dict) -> str | None:
    """Return a proposed default, preserving existing or explicitly supplied names."""
    default = _text(cfg.get('attendee_default'))
    if (not default or _internal(before.get('경비유형'), cfg)
            or not _internal(after.get('경비유형'), cfg)
            or _text(before.get('참석자')) or _text(after.get('참석자'))):
        return None
    return default


class AttendeeDefaults:
    """Add dependent cells to the originating table edit's undo record.

    tksheet 7.6 retains the event's cells mapping in its undo record. Using the
    same event keeps type and attendee in ONE undo/redo operation, not two.
    Replay callbacks cover both undo and redo in this pinned version.
    """
    def __init__(self, editor):
        self.editor = editor
        self.suspended = 0
        editor.table.extra_bindings('begin_undo', self._begin_replay)
        editor.table.extra_bindings('end_undo', self._end_replay)

    def _begin_replay(self, event):
        self.suspended += 1

    def _end_replay(self, event):
        self.suspended = max(0, self.suspended - 1)

    @contextmanager
    def pause(self):
        self.suspended += 1
        try:
            yield
        finally:
            self.suspended -= 1

    def apply(self, event) -> int:
        if self.suspended or not isinstance(event, dict):
            return 0
        editor, table = self.editor, self.editor.table
        if editor.loading or not event.get('eventname', '').startswith('edit_table'):
            return 0
        changed = event.get('cells', {}).get('table', {})
        type_col = editor.COLUMNS.index('경비유형')
        who_col = editor.COLUMNS.index('참석자')
        data = table.get_sheet_data()
        fills = []
        for (r, c), old_type in list(changed.items()):
            if c != type_col or not 0 <= r < len(data):
                continue
            who = data[r][who_col]
            before = {'경비유형': old_type, '참석자': changed.get((r, who_col), who)}
            after = {'경비유형': data[r][type_col], '참석자': who}
            value = attendee_on_type_change(before, after, editor.cfg)
            if value is not None:
                fills.append((r, value))
        for r, value in fills:
            table.event_data_set_table_cell(r, who_col, value, event, check_readonly=True)
        return len(fills)
