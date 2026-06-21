"""
tz.py — Puerto Rico time helpers (single source of truth for "today").

The app runs on Streamlit Community Cloud where the server clock is UTC, so
`datetime.now()` was several hours ahead of the user in Puerto Rico — pulling
*tomorrow's* games. PR is UTC-4 year-round (no DST), so a fixed offset is exact
and needs no tzdata dependency.

The "baseball day" rolls to the next date at 3 AM PR (not midnight), so a late
game — an 11 PM start ending ~2 AM, or even a 1 AM start — stays on the slate it
belongs to until 3 AM (req 2.8). Run `python tz.py` for the self-check.
"""

from datetime import datetime, timezone, timedelta, date

PR_TZ = timezone(timedelta(hours=-4))   # America/Puerto_Rico (AST, no DST)
ROLLOVER_HOUR = 3                        # slate rolls to the next date at 3 AM PR (2.8.3)


def now_pr() -> datetime:
    return datetime.now(PR_TZ)


def to_pr(commence_iso: str) -> datetime:
    """ISO timestamp ('...Z' or with offset) -> PR local time."""
    return datetime.fromisoformat(commence_iso.replace("Z", "+00:00")).astimezone(PR_TZ)


def baseball_date(moment: datetime | None = None) -> date:
    """Current MLB slate date in PR time, with the 3 AM rollover. Subtracting the
    rollover hours then taking the date does the wrap (incl. month/year) in one step."""
    m = (moment or now_pr()).astimezone(PR_TZ)
    return (m - timedelta(hours=ROLLOVER_HOUR)).date()


def game_slate_date(commence_iso: str) -> date:
    """Which baseball slate date a game belongs to, by its PR start time."""
    return baseball_date(to_pr(commence_iso))


def is_on_slate(commence_iso: str, slate: date) -> bool:
    """Does this game belong to the given slate date? Permissive on parse error."""
    try:
        return game_slate_date(commence_iso) == slate
    except Exception:
        return True


def is_upcoming(commence_iso: str, grace_minutes: int = 5) -> bool:
    """Has the game not started yet (within a grace window)? Permissive on error."""
    try:
        return to_pr(commence_iso) > now_pr() - timedelta(minutes=grace_minutes)
    except Exception:
        return True


if __name__ == "__main__":
    # 3 AM rollover: before 3 AM still belongs to the previous calendar day.
    assert baseball_date(datetime(2026, 6, 21, 2, 0, tzinfo=PR_TZ)) == date(2026, 6, 20)
    assert baseball_date(datetime(2026, 6, 21, 3, 0, tzinfo=PR_TZ)) == date(2026, 6, 21)
    assert baseball_date(datetime(2026, 6, 20, 23, 0, tzinfo=PR_TZ)) == date(2026, 6, 20)
    # The bug scenario: server clock is 01:00Z Jun 21, but it's 9 PM Jun 20 in PR.
    assert baseball_date(datetime(2026, 6, 21, 1, 0, tzinfo=timezone.utc)) == date(2026, 6, 20)
    # An 11:10 PM PR start (03:10Z next day) is the same slate as that evening.
    assert game_slate_date("2026-06-21T03:10:00Z") == date(2026, 6, 20)
    assert is_on_slate("2026-06-21T03:10:00Z", date(2026, 6, 20))
    print("tz self-check OK")
