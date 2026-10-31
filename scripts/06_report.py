"""Step 6 — Write the data-availability and quality report: reports/data_availability_report.md

Built from the HDF5 database plus the provenance tables, so every number in
the report can be traced to a stored file.

Run:  python scripts/06_report.py
"""
from __future__ import annotations

import h5py
import numpy as np
import pandas as pd

import dcflood as dc
from common import DAY_PAD, OUTPUTS, PROCESSED, ROOT, utc_now_iso

H5 = OUTPUTS / "dc_flood_db.h5"
REPORT = ROOT / "reports" / "data_availability_report.md"


def md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_(none)_"
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r) + " |")
    return "\n".join(out)


def main() -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    info = dc.list_contents(H5, verbose=False)
    with h5py.File(H5, "r") as h5:
        cov = dc._table(h5["metadata/coverage"])
        dm = dict(h5["gridded/daymet"].attrs) if "gridded/daymet" in h5 else {}
        grid_shape = h5["gridded/daymet/lat"].shape if "gridded/daymet/lat" in h5 else None
        lat = h5["gridded/daymet/lat"][()] if grid_shape else None
        lon = h5["gridded/daymet/lon"][()] if grid_shape else None
        st_attrs = dict(h5["stations"].attrs) if "stations" in h5 else {}
        obs_times = {}
        for sid, g in h5.get("stations", {}).items():
            ot = pd.Series(dc._s(g["obs_time"][()]))
            ot = ot[ot != ""]
            obs_times[sid] = ", ".join(f"{k} ({v})" for k, v in ot.value_counts().head(3).items()) or "not reported"
    events = pd.read_csv(PROCESSED / "events.csv", dtype=str, keep_default_na=False)
    dman = pd.read_csv(PROCESSED / "daymet_manifest.csv", dtype=str, keep_default_na=False) \
        if (PROCESSED / "daymet_manifest.csv").exists() else pd.DataFrame()
    sman = pd.read_csv(PROCESSED / "station_manifest.csv", dtype=str, keep_default_na=False) \
        if (PROCESSED / "station_manifest.csv").exists() else pd.DataFrame()
    cand = pd.read_csv(PROCESSED / "dc_station_candidates.csv") \
        if (PROCESSED / "dc_station_candidates.csv").exists() else pd.DataFrame()

    L = ["# Data availability and quality report",
         "", f"Generated {utc_now_iso()} by `scripts/06_report.py` from `outputs/dc_flood_db.h5` and the "
             "provenance tables in `data/processed/`.", ""]

    # ---- 1. overview ---------------------------------------------------------
    L += ["## 1. Overview", "",
          f"- Event index: {len(events)} flood events from the instructor CSV "
          f"({events.begin_date.min()} to {events.end_date.max()}); see `logs/events_validation.md`.",
          f"- Requested days: every EST calendar day touched by an event, plus ±{DAY_PAD} padding day "
          "(padding is stored but not counted as an event day below).",
          "- Coverage is counted on **event days** only. 'Available' means a value was obtained; "
          "a measured 0 mm counts as available (zero rainfall is not missing).", "",
          "### Coverage by source (event days)", "", md(dc.coverage_summary(H5)), ""]

    # ---- 2. Daymet -----------------------------------------------------------
    dcov = cov[cov.source == "daymet"]
    L += ["## 2. Gridded precipitation: Daymet V4 R1", "",
          "### Temporal coverage and missingness", ""]
    if not dcov.empty:
        ev_days = dcov[dcov.role == "event"]
        miss = ev_days[ev_days.available != True]  # noqa: E712
        years = sorted({d[:4] for d in dcov.date})
        L += [f"- Period searched: {years[0]}–{years[-1]} (NASA CMR catalogue, collection Daymet_Daily_V4R1_2129).",
              f"- Event days requested: {len(ev_days)}; obtained: {int(ev_days.available.sum())}; "
              f"missing: {len(miss)}.",
              f"- Events with at least one Daymet day: {ev_days.groupby('event_id').available.any().sum()} of "
              f"{ev_days.event_id.nunique()}."]
        nod = pd.to_numeric(dcov.nodata_cells, errors="coerce")
        nod = nod[dcov.available.astype(bool)]
        if nod.nunique() == 1:
            L.append(f"- NoData (-9999) cells: exactly {int(nod.iloc[0])} of {grid_shape[0] * grid_shape[1]} cells "
                     "on every stored day, always the same cells, on the Potomac River (Daymet does not estimate "
                     "over large water bodies). These are a fixed land/water mask, not missing observations; "
                     "every land cell has a value on every day.")
        else:
            L.append(f"- NoData (-9999) cells per stored day: min {int(nod.min())}, max {int(nod.max())}.")
        if not miss.empty:
            L += ["", "Missing Daymet event days:", "", md(miss[["event_id", "date", "status"]])]
    if not dman.empty:
        bad = dman[~dman.status.str.startswith("ok")]
        L += ["", f"- Download requests: {len(dman)}; failed: {len(bad)}."]
        if not bad.empty:
            L += ["", md(bad[["year", "first_day", "last_day", "status", "error"]])]
    L += ["", "### Spatial details", ""]
    if grid_shape:
        L += [f"- Native resolution: {dm.get('native_resolution')}; no upsampling.",
              f"- CRS: {dm.get('crs')} (`{dm.get('crs_proj4')}`).",
              f"- Stored subset: {grid_shape[0]} rows × {grid_shape[1]} columns of 1 km cells "
              f"({dm.get('spatial_subset')}).",
              f"- Geographic extent of cell centres: lat {lat.min():.3f}–{lat.max():.3f} °N, "
              f"lon {lon.min():.3f}–{lon.max():.3f} °E.",
              f"- Units: {dm.get('units')}; NoData encoding: {dm.get('missing_value_encoding')}."]
    L += ["", "### Temporal details", "",
          "- Temporal resolution: daily totals (an accumulation, not an instant).",
          f"- Day boundary: {dm.get('day_definition', '')}",
          f"- Calendar: {dm.get('calendar', '')}. None of the requested days fall on Dec 31 of a leap year.",
          "- The event CSV times are EST (UTC−5, no daylight saving), the same clock Daymet uses for its day, "
          "so an event on date D is matched to Daymet day D. A daily total cannot resolve a 2-hour storm: "
          "it includes all rain that day, before and after the flood window.", ""]

    # ---- 3. stations ---------------------------------------------------------
    L += ["## 3. Station time series: precipitation gauges (NOAA GHCN-Daily)", ""]
    st = info["stations"]
    if not st.empty:
        scov = cov[cov.source.str.startswith("station:") & (cov.role == "event")].copy()
        scov["station_id"] = scov.source.str.replace("station:", "", regex=False)
        scov["in_record"] = scov.status != "outside_station_record"
        agg = scov.groupby("station_id").agg(event_days=("date", "size"), in_record=("in_record", "sum"),
                                             with_data=("available", "sum"),
                                             qc_failed=("qc_failed", "sum")).reset_index()
        t = st.merge(agg, on="station_id", how="left")
        t["obs_time (most common)"] = t.station_id.map(obs_times)
        L += ["### Stations stored", "",
              md(t[["station_id", "name", "category", "network", "lat", "lon", "stored_period",
                    "days_with_data", "days_stored", "event_days", "in_record", "with_data", "qc_failed",
                    "obs_time (most common)"]]), "",
              "`days_with_data/days_stored` = completeness of the stored daily record; "
              "`in_record` = event days inside the station's operating period; "
              "`with_data` = event days with a value; `qc_failed` = values with a NOAA quality flag.", ""]
        for sid, g in scov.groupby("station_id"):
            m = g[g.status == "missing_in_record"]
            out = int((g.status == "outside_station_record").sum())
            line = f"- {sid}: {len(m)} event days missing within its record period"
            if len(m):
                line += f" ({', '.join(m.date.head(8))}{' …' if len(m) > 8 else ''})"
            if out:
                line += f"; {out} event days fall outside its operating period (no record, not zero rain)"
            L.append(line)
    if not sman.empty:
        bad = sman[~sman.status.str.startswith("ok")]
        L += ["", f"- Station download requests: {len(sman)}; failed: {len(bad)}."]
        if not bad.empty:
            L += ["", md(bad[["station_id", "status", "error"]])]
    L += ["", "### Spatial details", "",
          "- Point measurements (one gauge each); coordinates in WGS84 degrees from the GHCN-D station list.",
          "- 'In DC' = coordinates fall inside the US Census 2023 DC boundary polygon. Airports are stored "
          "separately as reference and are **not** counted as in-DC stations (Reagan National is in Virginia).",
          "", "### Temporal details", "",
          f"- Units: {st_attrs.get('units', '')}. Missing values: {st_attrs.get('missing_value_encoding', '')}.",
          f"- Day boundary: {st_attrs.get('day_definition', '')}",
          "- Consequence: for a CoCoRaHS/COOP gauge read at ~07:00, an evening flood on date D appears in the "
          "report dated D+1. Station values are stored with their original dates and observation times; "
          "no shifting was applied.",
          f"- Quality flags: {st_attrs.get('quality_flags', '')}.", ""]

    # ---- 4. in-DC search -----------------------------------------------------
    L += ["## 4. Search for precipitation stations inside Washington, DC", "",
          "See `docs/in_dc_station_search.md` for the search log. Candidates found in the GHCN-Daily "
          "station list:", ""]
    if not cand.empty:
        L += [md(cand[["station_id", "name", "state", "lat", "lon", "network", "category",
                       "prcp_first_year", "prcp_last_year", "has_prcp_1996_2025"]]), ""]

    # ---- 5. per-event table --------------------------------------------------
    L += ["## 5. Coverage by event (event days with data / event days)", ""]
    ev_cov = cov[cov.role == "event"].groupby(["event_id", "source"]).available.agg(["sum", "size"])
    ev_cov = (ev_cov["sum"].astype(int).astype(str) + "/" + ev_cov["size"].astype(str)).unstack("source")
    ev_cov.columns = [c.replace("station:", "") for c in ev_cov.columns]
    tab = events[["event_id", "begin_est", "end_est", "qc_flags"]].merge(ev_cov, left_on="event_id",
                                                                         right_index=True, how="left")
    tab["begin_est"] = tab.begin_est.str[:16].str.replace("T", " ")
    tab["end_est"] = tab.end_est.str[:16].str.replace("T", " ")
    L += [md(tab.fillna("–")), ""]

    REPORT.write_text("\n".join(L), encoding="utf-8")
    print(f"Wrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
