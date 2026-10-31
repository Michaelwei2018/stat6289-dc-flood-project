"""Step 5 — Demonstrate every access/plot function on a representative flood event.

Everything here reads ONLY outputs/dc_flood_db.h5 (no temporary downloads).

Outputs
  outputs/figures/daymet_<event>_<date>.png       raster maps for each event day
  outputs/figures/station_<id>_<event>.png         station time-series plots
  outputs/coverage_by_source.csv / coverage_by_event.csv
  outputs/demo_output.txt                          printed results of the demo

Run:  python scripts/05_demo.py            (default event E116, 2022-08-05 15:58–18:24 EST)
      python scripts/05_demo.py --event E059
"""
from __future__ import annotations

import argparse
import contextlib
import io

import matplotlib
matplotlib.use("Agg")  # save figures without opening windows
import matplotlib.pyplot as plt  # noqa: E402

import dcflood as dc  # noqa: E402
from common import OUTPUTS  # noqa: E402

H5 = OUTPUTS / "dc_flood_db.h5"
FIG = OUTPUTS / "figures"


def run(event_id: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    print("=" * 70 + "\n1. list_contents()\n" + "=" * 70)
    info = dc.list_contents(H5)

    print("\n" + "=" * 70 + f"\n2–3. get_raster() + plot_raster() for event {event_id}\n" + "=" * 70)
    import h5py
    with h5py.File(H5, "r") as h5:
        dates = [d.decode() if isinstance(d, bytes) else d for d in h5[f"gridded/daymet/{event_id}/date"][()]]
    for d in dates:
        r = dc.get_raster(H5, event_id, "daymet", d)
        vals = r["values"]
        print(f"{d} ({r['role']}): grid {vals.shape}, units {r['units']}, available={r['available']}, "
              f"max={float(__import__('numpy').nanmax(vals)) if r['available'] else 'n/a'}")
        dc.plot_raster(r, H5, save=str(FIG / f"daymet_{event_id}_{d}.png"))
        plt.close("all")

    print("\n" + "=" * 70 + f"\n4–5. get_timeseries() + plot_timeseries() for event {event_id}\n" + "=" * 70)
    st = info["stations"]
    for row in st.itertuples():
        s = dc.get_timeseries(H5, row.station_id, event_id, pad_days=3)
        print(f"\n{row.station_id} {row.name} [{row.category}]")
        print(s[["date", "prcp_mm", "mflag", "qflag", "obs_time", "missing"]].to_string(index=False))
        dc.plot_timeseries(s, save=str(FIG / f"station_{row.station_id}_{event_id}.png"))
        plt.close("all")

    print("\n" + "=" * 70 + "\n6. coverage_summary()\n" + "=" * 70)
    by_src = dc.coverage_summary(H5)
    by_ev = dc.coverage_summary(H5, by="event")
    print(by_src.to_string(index=False))
    by_src.to_csv(OUTPUTS / "coverage_by_source.csv", index=False)
    by_ev.to_csv(OUTPUTS / "coverage_by_event.csv", index=False)
    print(f"\nFigures saved in {FIG}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", default="E116")
    args = ap.parse_args()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run(args.event)
    text = buf.getvalue()
    print(text)
    (OUTPUTS / "demo_output.txt").write_text(text, encoding="utf-8")
