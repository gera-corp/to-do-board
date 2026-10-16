import calendar
from datetime import date, timedelta


def next_due(base: date, recurrence: str) -> date:
    """Следующая дата для повторяющейся задачи."""
    if recurrence == "daily":
        return base + timedelta(days=1)
    if recurrence == "weekly":
        return base + timedelta(weeks=1)
    if recurrence == "monthly":
        year = base.year + base.month // 12
        month = base.month % 12 + 1
        day = min(base.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    raise ValueError(f"unknown recurrence: {recurrence}")
