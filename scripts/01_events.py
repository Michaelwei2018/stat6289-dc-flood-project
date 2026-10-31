"""Step 1 — Validate the instructor's flood-event CSV and build the event index.

What this does
  * Reads data/raw/DC_flood_times.csv WITHOUT modifying it.
  * Assigns an event ID (E001, E002, ...) in file order.
  * Interprets the times as EST (fixed UTC-5, as the instructor specified) and
    adds UTC versions next to the originals.
  * Flags problems: unparseable values, zero/negative duration, overlaps.
  * Lists the calendar days (EST) each event touches, plus 1 padding day on
    each side, which is what we will request from the daily data sources.

Outputs
  data/processed/events.csv        one row per event (the event index)
  data/processed/event_days.csv    one row per (event, day) to request
  logs/events_validation.md        human-readable validation report

Run:  python scripts/01_events.py
"""
from __future__ import annotations

import calendar

import pandas as pd

from common import (DAY_PAD, EVENT_CSV, EVENT_TZ, LOGS, PROCESSED, ensure_dirs,
                    sha256_of, utc_now_iso)


def load_events() -> pd.DataFrame:
    raw = pd.read_csv(EVENT_CSV, dtype=str, keep_default_na=False)
    expected = ["begin_date", "begin_time", "end_date", "end_time"]
    if list(raw.columns) != expected:
        raise ValueError(f"Unexpected columns {list(raw.columns)}; expected {expected}")

    ev = raw.copy()
    ev.insert(0, "event_id", [f"E{i:03d}" for i in range(1, len(ev) + 1)])
    ev.insert(1, "csv_row", range(2, len(ev) + 2))  # row number in the CSV file (header = row 1)

    fmt = "%Y-%m-%d %H:%M"
    b = pd.to_datetime(ev.begin_date + " " + ev.begin_time, format=fmt, errors="coerce")
    e = pd.to_datetime(ev.end_date + " " + ev.end_time, format=fmt, errors="coerce")
    ev["parse_ok"] = b.notna() & e.notna()

    b_est = b.dt.tz_localize(EVENT_TZ)
    e_est = e.dt.tz_localize(EVENT_TZ)
    ev["begin_est"] = b_est.dt.strftime("%Y-%m-%dT%H:%M%z")
    ev["end_est"] = e_est.dt.strftime("%Y-%m-%dT%H:%M%z")
    ev["begin_utc"] = b_est.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%MZ")
    ev["end_utc"] = e_est.dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%MZ")
    ev["duration_min"] = ((e - b).dt.total_seconds() / 60).round().astype("Int64")

    # --- quality flags -----------------------------------------------------
    flags = [[] for _ in range(len(ev))]
    for i in range(len(ev)):
        if not ev.parse_ok.iat[i]:
            flags[i].append("unparseable")
            continue
        d = ev.duration_min.iat[i]
        if d == 0:
            flags[i].append("zero_duration")
        elif d < 0:
            flags[i].append("end_before_begin")
        if i > 0 and ev.parse_ok.iat[i - 1]:
            if b.iat[i] < e.iat[i - 1]:
                flags[i].append(f"overlaps_{ev.event_id.iat[i - 1]}")
            elif b.iat[i] == e.iat[i - 1]:
                flags[i].append(f"starts_when_{ev.event_id.iat[i - 1]}_ends")
            if b.iat[i] < b.iat[i - 1]:
                flags[i].append("out_of_order")
    ev["qc_flags"] = [";".join(f) for f in flags]
    ev["b_naive"], ev["e_naive"] = b, e
    return ev


def event_days(ev: pd.DataFrame) -> pd.DataFrame:
    """Calendar days (EST) touched by each event, plus DAY_PAD days either side."""
    rows = []
    for r in ev[ev.parse_ok].itertuples():
        first, last = r.b_naive.normalize(), r.e_naive.normalize()
        for day in pd.date_range(first - pd.Timedelta(days=DAY_PAD),
                                 last + pd.Timedelta(days=DAY_PAD), freq="D"):
            role = "event" if first <= day <= last else (
                "pad_before" if day < first else "pad_after")
            leap_dec31 = calendar.isleap(day.year) and day.month == 12 and day.day == 31
            rows.append({"event_id": r.event_id, "date_est": day.strftime("%Y-%m-%d"),
                         "role": role, "daymet_calendar_gap": leap_dec31})
    return pd.DataFrame(rows)


def write_report(ev: pd.DataFrame, days: pd.DataFrame) -> None:
    n = len(ev)
    flagged = ev[ev.qc_flags != ""]
    years = ev.b_naive.dt.year
    lines = [
        "# Event index validation report",
        "",
        f"- Generated: {utc_now_iso()} (by `scripts/01_events.py`)",
        f"- Source file: `data/raw/DC_flood_times.csv` (unchanged; SHA-256 `{sha256_of(EVENT_CSV)}`)",
        "- Columns: begin_date, begin_time, end_date, end_time (no IDs, locations or source columns supplied)",
        "- Time zone: EST, fixed UTC-5 all year (instructor's statement); UTC columns added as begin_utc/end_utc",
        f"- Events: {n}; parsed OK: {int(ev.parse_ok.sum())}; unparseable: {int((~ev.parse_ok).sum())}",
        f"- Period: {ev.b_naive.min():%Y-%m-%d} to {ev.e_naive.max():%Y-%m-%d} ({years.min()}–{years.max()}, {years.nunique()} distinct years)",
        f"- Duration (minutes): min {ev.duration_min.min()}, median {int(ev.duration_min.median())}, max {ev.duration_min.max()}",
        f"- Sorted chronologically: {bool(ev.b_naive.is_monotonic_increasing)}; duplicate rows: {int(ev.duplicated(subset=['begin_date','begin_time','end_date','end_time']).sum())}",
        f"- Distinct EST calendar days touched by events: {days[days.role=='event'].date_est.nunique()}",
        f"- Distinct days requested from daily sources (incl. ±{DAY_PAD} day padding): {days.date_est.nunique()}",
        f"- Requested days that do not exist in the Daymet 365-day calendar (Dec 31 of leap years): {int(days.daymet_calendar_gap.sum())}",
        "",
        "## Events with quality flags",
        "",
    ]
    if flagged.empty:
        lines.append("None.")
    else:
        lines += ["| event_id | CSV row | begin (EST) | end (EST) | duration (min) | flags |",
                  "|---|---|---|---|---|---|"]
        for r in flagged.itertuples():
            lines.append(f"| {r.event_id} | {r.csv_row} | {r.begin_date} {r.begin_time} | "
                         f"{r.end_date} {r.end_time} | {r.duration_min} | {r.qc_flags} |")
    lines += ["", "Flags are recorded, not corrected: all events are kept as supplied.", ""]
    (LOGS / "events_validation.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ensure_dirs()
    ev = load_events()
    days = event_days(ev)
    cols = ["event_id", "csv_row", "begin_date", "begin_time", "end_date", "end_time",
            "begin_est", "end_est", "begin_utc", "end_utc", "duration_min", "parse_ok", "qc_flags"]
    ev[cols].to_csv(PROCESSED / "events.csv", index=False)
    days.to_csv(PROCESSED / "event_days.csv", index=False)
    write_report(ev, days)
    print(f"{len(ev)} events -> data/processed/events.csv")
    print(f"{len(days)} event-day rows ({days.date_est.nunique()} distinct days) -> data/processed/event_days.csv")
    print("Report -> logs/events_validation.md")
    print("\nFlagged events:")
    print(ev.loc[ev.qc_flags != "", ["event_id", "begin_est", "end_est", "qc_flags"]].to_string(index=False))


if __name__ == "__main__":
    main()
