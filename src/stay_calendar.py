"""The worksheet uses the same range-calendar interaction as the main window."""
from .calendar_input import RangePicker


class StayCalendar(RangePicker):
    def __init__(self, parent, anchor, start, end, on_apply):
        super().__init__(parent, anchor, start, end, on_apply, stay=True)
