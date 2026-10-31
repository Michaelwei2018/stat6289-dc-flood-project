"""Step 4 — Build the single integrated HDF5 database: outputs/dc_flood_db.h5

Inputs (all produced by steps 1–3)
  data/raw/DC_flood_times.csv              instructor's event list (original, unchanged)
  data/processed/events.csv                event index with IDs, UTC times, QC flags
  data/processed/event_days.csv            (event, day) pairs requested from daily sources
  data/processed/daymet_day_status.csv     which requested Daymet days were obtained
  data/processed/daymet_manifest.csv       provenance of every Daymet request
  data/downloads/daymet/*.nc4              small DC subsets of Daymet
  data/processed/dc_station_candidates.csv stations found inside DC (+ airports)
  data/processed/station_manifest.csv      provenance of every station request
  data/downloads/ghcnd/<id>.csv(.gz)       station records
  data/reference/dc_boundary.geojson       DC outline (for maps)

HDF5 layout (explained in README.md)
  /                       file-level description
  /events/                flood-event index (one entry per CSV row)
  /gridded/daymet/        shared grid coordinates + one subgroup per event
  /stations/<station_id>/ one daily precipitation record per station
  /metadata/              provenance tables, DC boundary, coverage summary

Run:  python scripts/04_build_hdf5.py
"""
from __future__ import annotations

import gzip
import json
import platform
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

from common import (BUFFER_KM, DAYMET_DOI, DAYMET_PROJ4, DOWNLOADS, EVENT_CSV, OUTPUTS,
                    PROCESSED, REFERENCE, ensure_dirs, sha256_of, utc_now_iso)

H5_PATH = OUTPUTS / "dc_flood_db.h5"
FILL = np.float32(-9999.0)            # Daymet's own NoData code, kept as-is
GZ = dict(compression="gzip", compression_opts=4, shuffle=True)
STR = h5py.string_dtype(encoding="utf-8")


def sarr(values) -> np.ndarray:
    """Python strings -> array h5py can store as variable-length UTF-8 strings."""
    return np.array([("" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v))
                     for v in values], dtype=object)


def put_table(group: h5py.Group, df: pd.DataFrame, numeric_float=(), numeric_int=()) -> None:
    """Store a table column-by-column (each column = one 1-D dataset)."""
    for col in df.columns:
        if col in numeric_float:
            group.create_dataset(col, data=df[col].astype("float64").to_numpy())
        elif col in numeric_int:
            group.create_dataset(col, data=df[col].astype("int64").to_numpy())
        elif df[col].dtype == bool:
            group.create_dataset(col, data=df[col].to_numpy(dtype=bool))
        else:
            group.create_dataset(col, data=sarr(df[col]), dtype=STR)
    group.attrs["columns"] = json.dumps(list(df.columns))
    group.attrs["n_rows"] = len(df)


def put_text(group: h5py.Group, name: str, text: str, **attrs) -> None:
    ds = group.create_dataset(name, data=text, dtype=STR)
    for k, v in attrs.items():
        ds.attrs[k] = v


# ----------------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------------
def write_events(h5: h5py.File) -> pd.DataFrame:
    ev = pd.read_csv(PROCESSED / "events.csv", dtype=str, keep_default_na=False)
    g = h5.create_group("events")
    g.attrs["description"] = ("Flood-event index from the instructor-supplied CSV. One entry per CSV row, "
                              "in file order. Event IDs (E001...) were assigned by this project.")
    g.attrs["time_zone_of_original_times"] = "EST, fixed UTC-5 all year (no daylight saving), per instructor"
    g.attrs["utc_conversion"] = "begin_utc/end_utc = original EST time + 5 h"
    g.attrs["epoch_columns"] = "begin_utc_epoch_s/end_utc_epoch_s: seconds since 1970-01-01T00:00:00Z"
    ev["begin_utc_epoch_s"] = (pd.to_datetime(ev.begin_utc, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")
    ev["end_utc_epoch_s"] = (pd.to_datetime(ev.end_utc, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")
    ev["duration_min"] = pd.to_numeric(ev.duration_min)
    ev["parse_ok"] = ev.parse_ok.eq("True")
    put_table(g, ev, numeric_int=("begin_utc_epoch_s", "end_utc_epoch_s", "duration_min", "csv_row"))
    put_text(g, "original_csv", EVENT_CSV.read_text(encoding="utf-8"),
             description="Exact text of the instructor-supplied file (preserved unchanged)",
             source_file="data/raw/DC_flood_times.csv", sha256=sha256_of(EVENT_CSV))
    return ev


# ----------------------------------------------------------------------------
# Daymet
# ----------------------------------------------------------------------------
def write_daymet(h5: h5py.File, ev: pd.DataFrame) -> pd.DataFrame:
    import netCDF4
    status_path = PROCESSED / "daymet_day_status.csv"
    if not status_path.exists():
        print("No Daymet status table found — skipping Daymet (run scripts/02_daymet.py).")
        return pd.DataFrame()
    status = pd.read_csv(status_path, dtype=str, keep_default_na=False)
    ddir = DOWNLOADS / "daymet"
    ok_files = sorted({f for f in status.file if f})
    if not ok_files:
        print("Daymet: no files downloaded — storing availability information only.")

    # Read every downloaded subset once: {date: 2-D array}
    by_date, grid, var_attrs, crs_attrs = {}, None, {}, {}
    for fname in ok_files:
        with netCDF4.Dataset(ddir / fname) as ds:
            ds.set_auto_mask(True)
            t = ds.variables["time"]
            dates = netCDF4.num2date(t[:], t.units, getattr(t, "calendar", "standard"))
            p = ds.variables["prcp"]
            arr = np.ma.filled(p[:].astype("float32"), FILL)
            for i, d in enumerate(dates):
                by_date[f"{d.year:04d}-{d.month:02d}-{d.day:02d}"] = arr[i]
            if grid is None:
                grid = {k: np.asarray(ds.variables[k][:]) for k in ("x", "y", "lat", "lon")}
                var_attrs = {a: p.getncattr(a) for a in p.ncattrs()}
                if "lambert_conformal_conic" in ds.variables:
                    v = ds.variables["lambert_conformal_conic"]
                    crs_attrs = {a: v.getncattr(a) for a in v.ncattrs()}

    g = h5.create_group("gridded/daymet")
    g.attrs.update({
        "source": "Daymet: Daily Surface Weather Data on a 1-km Grid for North America, Version 4 R1 (ORNL DAAC)",
        "doi": DAYMET_DOI,
        "variable": "prcp (daily total precipitation)",
        "units": "mm/day",
        "missing_value_encoding": float(FILL),
        "missing_value_note": "-9999 = Daymet NoData (_FillValue) or day not obtained; see 'available' per event",
        "native_resolution": "1 km x 1 km (no resampling or reprojection performed)",
        "crs": "Lambert Conformal Conic, WGS84 datum",
        "crs_proj4": DAYMET_PROJ4,
        "crs_attributes_from_file": json.dumps({k: (v.tolist() if hasattr(v, "tolist") else v)
                                                for k, v in crs_attrs.items()}, default=str),
        "spatial_subset": f"Washington, DC bounding box + {BUFFER_KM} km buffer, native grid cells",
        "temporal_resolution": "daily",
        "day_definition": ("Daymet V4 targets the 24 h from midnight to midnight local standard time "
                           "(EST for DC); station observation-time corrections are approximate "
                           "(Thornton et al. 2021), so +/-1 padding day is stored around each event."),
        "calendar": "365-day: in leap years Dec 31 is not provided by Daymet",
        "retrieval": "NASA Earthdata OPeNDAP index-range subsets of daymet_v4_daily_na_prcp_<year>.nc",
        "variable_attributes_from_file": json.dumps({k: (v.tolist() if hasattr(v, "tolist") else v)
                                                     for k, v in var_attrs.items()}, default=str),
    })
    if grid is not None:
        for k, units, desc in (("x", "m", "projected x coordinate of cell centres (LCC)"),
                               ("y", "m", "projected y coordinate of cell centres (LCC)"),
                               ("lat", "degrees_north", "latitude of cell centres"),
                               ("lon", "degrees_east", "longitude of cell centres")):
            ds = g.create_dataset(k, data=grid[k].astype("float64"))
            ds.attrs.update(units=units, description=desc)
        ny, nx = grid["lat"].shape
    rows = []
    for event_id, sub in status.groupby("event_id", sort=True):
        eg = g.create_group(event_id)
        dates = sub.date_est.tolist()
        avail = np.array([d in by_date for d in dates])
        eg.create_dataset("date", data=sarr(dates), dtype=STR).attrs[
            "description"] = "Daymet day label (YYYY-MM-DD), local standard time day"
        eg.create_dataset("role", data=sarr(sub.role), dtype=STR).attrs[
            "description"] = "event = day touched by the flood window; pad_before/pad_after = +/-1 day"
        eg.create_dataset("available", data=avail)
        eg.create_dataset("status", data=sarr(sub.status), dtype=STR)
        if grid is not None:
            cube = np.full((len(dates), ny, nx), FILL, dtype="float32")
            for i, d in enumerate(dates):
                if d in by_date:
                    cube[i] = by_date[d]
            ds = eg.create_dataset("prcp", data=cube, chunks=(1, ny, nx), fillvalue=FILL, **GZ)
            ds.attrs.update(units="mm/day", dims="date, y, x", _FillValue=FILL,
                            description="Daily precipitation on the native Daymet 1 km grid")
        r = ev[ev.event_id == event_id].iloc[0]
        eg.attrs.update(begin_est=r.begin_est, end_est=r.end_est)
        for d, a, role, st in zip(dates, avail, sub.role, sub.status):
            nod = int((by_date[d] == FILL).sum()) if a else None
            rows.append({"source": "daymet", "event_id": event_id, "date": d, "role": role,
                         "available": bool(a), "status": st, "nodata_cells": nod})
    print(f"Daymet: stored {len(status.event_id.unique())} events; "
          f"{int(sum(r['available'] for r in rows))}/{len(rows)} requested event-days available")
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Stations (NOAA GHCN-Daily)
# ----------------------------------------------------------------------------
def read_station_file(sid: str) -> tuple[pd.DataFrame, str]:
    """Return (daily table, format) with columns date, prcp_mm, mflag, qflag, sflag, obs_time."""
    ddir = DOWNLOADS / "ghcnd"
    ads, bys = ddir / f"{sid}.csv", ddir / f"{sid}.csv.gz"
    if ads.exists():
        df = pd.read_csv(ads, dtype=str, keep_default_na=False)
        if "PRCP" not in df.columns:
            return pd.DataFrame(), "ads-empty"
        attrs = df.get("PRCP_ATTRIBUTES", pd.Series([""] * len(df)))
        parts = attrs.str.split(",", expand=True).reindex(columns=range(4)).fillna("")
        out = pd.DataFrame({"date": df.DATE.str[:10],
                            "prcp_mm": pd.to_numeric(df.PRCP, errors="coerce"),
                            "mflag": parts[0], "qflag": parts[1], "sflag": parts[2], "obs_time": parts[3]})
        return out, "NCEI Access Data Service CSV (mm)"
    if bys.exists():
        with gzip.open(bys, "rt") as f:
            df = pd.read_csv(f, header=None, dtype=str, keep_default_na=False,
                             names=["id", "date", "element", "value", "mflag", "qflag", "sflag", "obs_time"])
        df = df[df.element == "PRCP"]
        out = pd.DataFrame({"date": pd.to_datetime(df.date, format="%Y%m%d").dt.strftime("%Y-%m-%d"),
                            "prcp_mm": pd.to_numeric(df.value, errors="coerce") / 10.0,  # tenths of mm -> mm
                            "mflag": df.mflag, "qflag": df.qflag, "sflag": df.sflag, "obs_time": df.obs_time})
        return out, "GHCN-Daily by_station CSV (tenths of mm, converted to mm)"
    return pd.DataFrame(), "missing"


def write_stations(h5: h5py.File, ev: pd.DataFrame) -> pd.DataFrame:
    cand_p, man_p = PROCESSED / "dc_station_candidates.csv", PROCESSED / "station_manifest.csv"
    if not cand_p.exists():
        print("No station candidates table — skipping stations (run scripts/03_stations.py).")
        return pd.DataFrame()
    cand = pd.read_csv(cand_p, dtype={"station_id": str})
    man = pd.read_csv(man_p, dtype=str, keep_default_na=False) if man_p.exists() else pd.DataFrame()
    days = pd.read_csv(PROCESSED / "event_days.csv", dtype=str)
    g = h5.create_group("stations")
    g.attrs.update({
        "source": "NOAA NCEI Global Historical Climatology Network - Daily (GHCN-Daily), element PRCP",
        "units": "mm (daily total)",
        "missing_value_encoding": "NaN (no observation for that date); 0.0 means measured zero rainfall",
        "temporal_resolution": "daily accumulation",
        "day_definition": ("First-order/airport stations (USW): midnight-to-midnight local standard time. "
                           "COOP (USC) and CoCoRaHS (US1): 24 h ending at the observation time (obs_time, "
                           "often ~07:00 local) on the stated date, so most of the rain belongs to the previous "
                           "calendar day."),
        "quality_flags": ("mflag: measurement flag (T = trace, ...); qflag: blank = passed all QC checks, "
                          "non-blank = failed a check; sflag: data source; see GHCN-Daily readme"),
        "selection": "in_DC = coordinates inside US Census 2023 DC polygon; airports kept separately as reference",
    })
    rows = []
    for r in cand.itertuples():
        m = man[man.station_id == r.station_id] if not man.empty else pd.DataFrame()
        if m.empty or not m.iloc[0].status.startswith("ok"):
            continue
        m = m.iloc[0]
        data, fmt = read_station_file(r.station_id)
        if data.empty:
            print(f"  {r.station_id}: no PRCP rows in download")
            continue
        full = pd.DataFrame({"date": pd.date_range(m.request_start, m.request_end, freq="D").strftime("%Y-%m-%d")})
        data = full.merge(data.drop_duplicates("date"), on="date", how="left")
        sg = g.create_group(r.station_id)
        sg.attrs.update({
            "name": str(r.name), "network": str(r.network), "category": str(r.category),
            "latitude": float(r.lat), "longitude": float(r.lon), "elevation_m": float(r.elev_m),
            "inside_dc_polygon": bool(r.inside_dc_polygon), "state_code": str(r.state),
            "prcp_record_first_year": int(r.prcp_first_year), "prcp_record_last_year": int(r.prcp_last_year),
            "stored_period": f"{m.request_start} to {m.request_end}",
            "retrieval_method": m.get("method", "NCEI Access Data Service"), "retrieval_url": m.url,
            "retrieved_utc": m.retrieved_utc, "download_sha256": m.sha256, "file_format": fmt,
        })
        sg.create_dataset("date", data=sarr(data.date), dtype=STR, **GZ).attrs[
            "description"] = "observation date (YYYY-MM-DD) as reported"
        ds = sg.create_dataset("prcp_mm", data=data.prcp_mm.to_numpy("float32"), **GZ)
        ds.attrs.update(units="mm", missing_value="NaN", description="daily precipitation total")
        for col in ("mflag", "qflag", "sflag", "obs_time"):
            sg.create_dataset(col, data=sarr(data[col].fillna("")), dtype=STR, **GZ)
        # coverage on each event's days
        lookup = data.set_index("date")
        for d in days.itertuples():
            in_period = m.request_start <= d.date_est <= m.request_end
            v = lookup.prcp_mm.get(d.date_est, np.nan) if in_period else np.nan
            q = lookup.qflag.get(d.date_est, "") if in_period else ""
            st = ("obtained" if pd.notna(v) else "missing_in_record") if in_period else "outside_station_record"
            rows.append({"source": f"station:{r.station_id}", "category": r.category, "event_id": d.event_id,
                         "date": d.date_est, "role": d.role, "available": bool(pd.notna(v)), "status": st,
                         "qc_failed": bool(isinstance(q, str) and q.strip() != "")})
        print(f"  {r.station_id} ({r.category}): {int(data.prcp_mm.notna().sum())}/{len(data)} days with data")
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Metadata
# ----------------------------------------------------------------------------
def write_metadata(h5: h5py.File, coverage: pd.DataFrame) -> None:
    g = h5.create_group("metadata")
    g.attrs.update(created_utc=utc_now_iso(), created_by="scripts/04_build_hdf5.py",
                   python=platform.python_version(), h5py=h5py.__version__,
                   numpy=np.__version__, pandas=pd.__version__)
    for name, path in (("events_validation_report", Path("logs/events_validation.md")),):
        p = PROCESSED.parents[1] / path
        if p.exists():
            put_text(g, name, p.read_text(encoding="utf-8"))
    for name in ("daymet_manifest", "station_manifest", "dc_station_candidates"):
        p = PROCESSED / f"{name}.csv"
        if p.exists():
            put_text(g, name + "_csv", p.read_text(encoding="utf-8"),
                     description=f"Provenance table {p.name} stored as CSV text")
    b = REFERENCE / "dc_boundary.geojson"
    if b.exists():
        put_text(g, "dc_boundary_geojson", b.read_text(encoding="utf-8"),
                 description="Washington, DC outline (EPSG:4326) from US Census 2023 cartographic boundary file",
                 crs="EPSG:4326")
    if not coverage.empty:
        cg = g.create_group("coverage")
        cg.attrs["description"] = "One row per (source, event, requested day): was usable data obtained?"
        cov = coverage.copy()
        for c in ("nodata_cells",):
            if c in cov:
                cov[c] = cov[c].fillna(-1).astype("int64")
        for c in ("available", "qc_failed"):
            if c in cov:
                cov[c] = cov[c].fillna(False).astype(bool)
        put_table(cg, cov.fillna(""), numeric_int=("nodata_cells",) if "nodata_cells" in cov else ())


def main() -> None:
    ensure_dirs()
    if H5_PATH.exists():
        H5_PATH.unlink()
    with h5py.File(H5_PATH, "w") as h5:
        h5.attrs.update({
            "title": "Environmental observations for documented Washington, DC flood events",
            "course": "STAT 6289 Computing Environments — individual project (Version 3: Daymet + DC precipitation stations)",
            "layout": "/events, /gridded/daymet, /stations/<station_id>, /metadata (see README.md)",
            "created_utc": utc_now_iso(),
        })
        ev = write_events(h5)
        cov_d = write_daymet(h5, ev)
        print("Stations:")
        cov_s = write_stations(h5, ev)
        coverage = pd.concat([cov_d, cov_s], ignore_index=True)
        write_metadata(h5, coverage)
    coverage.to_csv(PROCESSED / "coverage_by_day.csv", index=False)
    size_mb = H5_PATH.stat().st_size / 1e6
    print(f"\nWrote {H5_PATH.relative_to(OUTPUTS.parent)} ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
