# Event index validation report

- Generated: 2026-10-31T00:24:18+00:00 (by `scripts/01_events.py`)
- Source file: `data/raw/DC_flood_times.csv` (unchanged; SHA-256 `833ab92d92c78c8f93b2de05ca7730676c6f9f480cad684a1586a3c6e218cfb8`)
- Columns: begin_date, begin_time, end_date, end_time (no IDs, locations or source columns supplied)
- Time zone: EST, fixed UTC-5 all year (instructor's statement); UTC columns added as begin_utc/end_utc
- Events: 125; parsed OK: 125; unparseable: 0
- Period: 1996-01-19 to 2025-07-19 (1996–2025, 28 distinct years)
- Duration (minutes): min 0, median 155, max 4170
- Sorted chronologically: True; duplicate rows: 0
- Distinct EST calendar days touched by events: 149
- Distinct days requested from daily sources (incl. ±1 day padding): 372
- Requested days that do not exist in the Daymet 365-day calendar (Dec 31 of leap years): 0

## Events with quality flags

| event_id | CSV row | begin (EST) | end (EST) | duration (min) | flags |
|---|---|---|---|---|---|
| E072 | 73 | 2016-07-20 01:30 | 2016-07-20 07:30 | 360 | starts_when_E071_ends |
| E075 | 76 | 2017-07-29 02:00 | 2017-07-29 02:15 | 15 | overlaps_E074 |
| E094 | 95 | 2019-11-01 02:15 | 2019-11-01 02:15 | 0 | zero_duration |
| E112 | 113 | 2022-07-03 00:44 | 2022-07-03 03:50 | 186 | overlaps_E111 |

Flags are recorded, not corrected: all events are kept as supplied.
