"""US equity regular-session calendar (NYSE/Nasdaq) with holidays, early closes and DST (via zoneinfo).

Sources: NYSE published holiday calendar 2025-2027 (regular holidays; early closes 13:00 ET on the day after
Thanksgiving, Christmas Eve when it falls on a weekday, and July 3 when July 4 is a weekday). Cross-checked against
the `us-equities` calendar committed in github.com/zeeshan8281/kerb (config/calendars/us-equities.json), which lists
the same 2026-2027 full-day holidays. Times are stored in UTC; session times are expressed in America/New_York.
"""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd

ET = ZoneInfo("America/New_York")
TBILISI = ZoneInfo("Asia/Tbilisi")

FULL_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026 (matches kerb us-equities.json)
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027 (matches kerb us-equities.json)
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
}
# kerb lists 2027-04-02 (Good Friday 2027 is March 26, 2027; April 2 would be wrong). Easter 2027 = March 28 ->
# Good Friday = March 26. We keep March 26 and flag the discrepancy in DATA_AUDIT.md.
EARLY_CLOSES = {  # 13:00 ET
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
    "2027-11-26",  # 2027-07-02? July 4 2027 is a Sunday, observed Monday July 5; NYSE typically early-closes the Friday before? No rule: only when July 4 is a weekday. Leave out.
}
REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)


def session_for_date(d: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
    """Return (open, close) as tz-aware UTC datetimes for a trading date, or None if the market is closed."""
    if d.weekday() >= 5 or d.isoformat() in FULL_HOLIDAYS:
        return None
    close_t = EARLY_CLOSE if d.isoformat() in EARLY_CLOSES else REGULAR_CLOSE
    o = dt.datetime.combine(d, REGULAR_OPEN, tzinfo=ET).astimezone(dt.timezone.utc)
    c = dt.datetime.combine(d, close_t, tzinfo=ET).astimezone(dt.timezone.utc)
    return o, c


def session_frame(start: dt.date, end: dt.date) -> pd.DataFrame:
    rows = []
    d = start
    while d <= end:
        s = session_for_date(d)
        if s:
            rows.append({"date": d, "open_utc": pd.Timestamp(s[0]), "close_utc": pd.Timestamp(s[1]), "early_close": d.isoformat() in EARLY_CLOSES})
        d += dt.timedelta(days=1)
    return pd.DataFrame(rows)


def annotate(ts: pd.Series, entry_cutoff_min: int = 90, exit_buffer_min: int = 30) -> pd.DataFrame:
    """For a series of UTC timestamps (decision/observation times) return session flags:
    in_session: open <= ts < close; can_enter: in_session and ts <= close - entry_cutoff; must_exit: ts >= close - exit_buffer
    (or not in session); session_date: the ET date of the session (NaT if closed)."""
    ts = pd.to_datetime(ts, utc=True)
    et = ts.dt.tz_convert(ET)
    dates = et.dt.date
    uniq = sorted(set(dates))
    sess = {d: session_for_date(d) for d in uniq}
    open_utc = pd.Series([pd.Timestamp(sess[d][0]) if sess[d] else pd.NaT for d in dates], index=ts.index)
    close_utc = pd.Series([pd.Timestamp(sess[d][1]) if sess[d] else pd.NaT for d in dates], index=ts.index)
    in_session = (ts >= open_utc) & (ts < close_utc)
    can_enter = in_session & (ts <= close_utc - pd.Timedelta(minutes=entry_cutoff_min))
    must_exit = ~in_session | (ts >= close_utc - pd.Timedelta(minutes=exit_buffer_min))
    return pd.DataFrame({"in_session": in_session.fillna(False).astype(bool), "can_enter": can_enter.fillna(False).astype(bool),
                         "must_exit": must_exit.fillna(True).astype(bool), "session_date": pd.Series([d if sess[d] else None for d in dates], index=ts.index),
                         "session_open": open_utc, "session_close": close_utc})


def fmt_et(ts) -> str:
    t = pd.Timestamp(ts)
    return f"{t.tz_convert(ET):%Y-%m-%d %H:%M ET} / {t.tz_convert(TBILISI):%H:%M Tbilisi}"
