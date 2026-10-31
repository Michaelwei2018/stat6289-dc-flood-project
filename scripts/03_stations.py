"""Step 3 — Find rain gauges physically inside Washington, DC and download their
daily precipitation (NOAA GHCN-Daily).

How it works (plain words)
  NOAA's Global Historical Climatology Network – Daily (GHCN-Daily) collects
  daily weather observations from tens of thousands of stations, including
  NWS cooperative observers (IDs starting USC), airports/first-order stations
  (USW) and CoCoRaHS volunteer rain gauges (US1).

  1. Download NOAA's station list and inventory (which variables each station
     measured, and in which years).
  2. Download the official DC boundary (US Census) and keep only stations whose
     coordinates fall INSIDE it. Airports (DCA, IAD, BWI) are kept in a separate
     "reference, outside DC" list, as the assignment requires.
  3. For every in-DC station with precipitation (PRCP) in 1996–2025, download
     the daily PRCP series with NOAA's Access Data Service (units = mm),
     including quality flags and observation time.

Outputs
  data/reference/dc_boundary.geojson           DC outline (also used for maps)
  data/processed/dc_station_candidates.csv     every GHCN-D station inside DC (+ airports)
  data/processed/station_manifest.csv          one row per download attempt
  data/downloads/ghcnd/<station>.csv           raw downloads (not committed)

Run:  python scripts/03_stations.py
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

from common import (DC_BBOX, DOWNLOADS, PROCESSED, REFERENCE, append_jsonl, ensure_dirs,
                    sha256_of, utc_now_iso, LOGS)

GHCN_BASE = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/"
ADS_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
CENSUS_STATES = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip"
AIRPORTS = {"USW00013743": "Reagan National (DCA)",
            "USW00093738": "Dulles (IAD)",
            "USW00093721": "BWI"}
NETWORK = {"USC": "NWS Cooperative Observer (COOP)", "USW": "First-order / airport (WBAN)",
           "US1": "CoCoRaHS volunteer", "USR": "RAWS", "USS": "SNOTEL"}
OUT_DIR = DOWNLOADS / "ghcnd"
RUN_LOG = LOGS / "station_requests.jsonl"


def fetch(url: str, dest: Path, params: dict | None = None, purpose: str = "") -> dict:
    """Download url -> dest (skips if already present). Returns a provenance record."""
    rec = {"time_utc": utc_now_iso(), "purpose": purpose, "url": url, "params": params or {},
           "file": dest.name}
    if dest.exists() and dest.stat().st_size > 0:
        rec.update(status="ok (already downloaded)", bytes=dest.stat().st_size, sha256=sha256_of(dest))
        return rec
    err = ""
    for attempt in range(1, 4):
        try:
            r = requests.get(url, params=params, timeout=180)
            if r.status_code == 200 and r.content:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                rec.update(status="ok", final_url=r.url, bytes=len(r.content), sha256=sha256_of(dest))
                append_jsonl(RUN_LOG, rec)
                return rec
            err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        time.sleep(3 * attempt)
    rec.update(status="failed", error=err)
    append_jsonl(RUN_LOG, rec)
    return rec


def read_station_list() -> pd.DataFrame:
    p = OUT_DIR / "ghcnd-stations.txt"
    fetch(GHCN_BASE + "ghcnd-stations.txt", p, purpose="GHCN-D station list")
    cols = [(0, 11), (12, 20), (21, 30), (31, 37), (38, 40), (41, 71), (72, 75), (76, 79), (80, 85)]
    names = ["station_id", "lat", "lon", "elev_m", "state", "name", "gsn", "hcn_crn", "wmo"]
    return pd.read_fwf(p, colspecs=cols, names=names, dtype={"wmo": str})


def read_inventory() -> pd.DataFrame:
    p = OUT_DIR / "ghcnd-inventory.txt"
    fetch(GHCN_BASE + "ghcnd-inventory.txt", p, purpose="GHCN-D inventory")
    cols = [(0, 11), (12, 20), (21, 30), (31, 35), (36, 40), (41, 45)]
    inv = pd.read_fwf(p, colspecs=cols, names=["station_id", "lat", "lon", "element", "first_year", "last_year"])
    return inv[inv.element == "PRCP"][["station_id", "first_year", "last_year"]].rename(
        columns={"first_year": "prcp_first_year", "last_year": "prcp_last_year"})


def dc_boundary():
    """Return DC polygon (EPSG:4326) and save it as GeoJSON for later maps."""
    import geopandas as gpd
    out = REFERENCE / "dc_boundary.geojson"
    if out.exists():
        return gpd.read_file(out)
    zpath = DOWNLOADS / "census" / "cb_2023_us_state_500k.zip"
    rec = fetch(CENSUS_STATES, zpath, purpose="US Census cartographic state boundaries 2023 (1:500k)")
    if not rec["status"].startswith("ok"):
        return None
    states = gpd.read_file(zpath)
    dc = states[states.STATEFP == "11"].to_crs("EPSG:4326")[["NAME", "STATEFP", "geometry"]]
    dc["source"] = "US Census Bureau, 2023 Cartographic Boundary File cb_2023_us_state_500k"
    dc.to_file(out, driver="GeoJSON")
    return dc


def main() -> None:
    ensure_dirs()
    st = read_station_list()
    inv = read_inventory()

    # rough box first (fast), then exact point-in-polygon test
    pad = 0.05
    box = st[st.lat.between(DC_BBOX["south"] - pad, DC_BBOX["north"] + pad)
             & st.lon.between(DC_BBOX["west"] - pad, DC_BBOX["east"] + pad)].copy()
    dc = dc_boundary()
    have_polygon = dc is not None
    if have_polygon:
        import geopandas as gpd
        pts = gpd.GeoDataFrame(box, geometry=gpd.points_from_xy(box.lon, box.lat), crs="EPSG:4326")
        box["inside_dc_polygon"] = pts.within(dc.geometry.iloc[0]).values
    else:
        print("WARNING: DC boundary unavailable; falling back to the station list's state code.")
        box["inside_dc_polygon"] = box.state.eq("DC")
    box["state_code_dc"] = box.state.eq("DC")

    cand = box[box.inside_dc_polygon | box.state_code_dc | box.station_id.isin(AIRPORTS)]
    extra = st[st.station_id.isin(AIRPORTS) & ~st.station_id.isin(cand.station_id)].copy()
    extra["inside_dc_polygon"] = False
    cand = pd.concat([cand, extra], ignore_index=True)
    cand = cand.merge(inv, on="station_id", how="left")
    cand["network"] = cand.station_id.str[:3].map(NETWORK).fillna("other")
    cand["category"] = "in_DC"
    cand.loc[~cand.inside_dc_polygon.astype(bool), "category"] = "state_code_DC_but_outside_polygon"
    cand.loc[cand.station_id.isin(AIRPORTS), "category"] = "airport_reference_outside_DC"
    cand["dc_test"] = "DC polygon (US Census 2023)" if have_polygon else "state code only"
    cand["has_prcp_1996_2025"] = ((cand.prcp_last_year >= 1996) & (cand.prcp_first_year <= 2025)).fillna(False).astype(bool)
    cand = cand.sort_values(["category", "prcp_first_year"])
    keep = ["station_id", "name", "state", "lat", "lon", "elev_m", "network", "category",
            "inside_dc_polygon", "dc_test", "prcp_first_year", "prcp_last_year", "has_prcp_1996_2025"]
    cand[keep].to_csv(PROCESSED / "dc_station_candidates.csv", index=False)

    print(f"Stations within the DC area box: {len(box)}")
    print(f"In-DC stations (inside DC polygon): {int((cand.category == 'in_DC').sum())}; "
          f"with PRCP overlapping 1996–2025: {int(((cand.category == 'in_DC') & cand.has_prcp_1996_2025).sum())}")
    print(cand[keep].to_string(index=False))

    # ---------------- download daily PRCP --------------------------------
    days = pd.read_csv(PROCESSED / "event_days.csv")
    start, end = days.date_est.min(), days.date_est.max()
    todo = cand[cand.has_prcp_1996_2025 & cand.category.isin(["in_DC", "airport_reference_outside_DC"])]
    manifest = []
    for r in todo.itertuples():
        s = max(start, f"{int(r.prcp_first_year)}-01-01")
        e = min(end, f"{int(r.prcp_last_year)}-12-31")
        params = {"dataset": "daily-summaries", "stations": r.station_id, "dataTypes": "PRCP",
                  "startDate": s, "endDate": e, "format": "csv", "units": "metric",
                  "includeAttributes": "true", "includeStationName": "true",
                  "includeStationLocation": "1"}
        rec = fetch(ADS_URL, OUT_DIR / f"{r.station_id}.csv", params=params,
                    purpose=f"PRCP {r.station_id} {s}..{e} (NCEI Access Data Service)")
        method = "NCEI Access Data Service (daily-summaries, units=metric)"
        n = ""
        if rec["status"].startswith("ok"):
            try:
                n = len(pd.read_csv(OUT_DIR / f"{r.station_id}.csv"))
            except Exception:
                n = "unreadable"
        if not rec["status"].startswith("ok") or n in ("", "unreadable", 0):
            # Fallback: NOAA's static per-station file (all elements, tenths of mm)
            bad = OUT_DIR / f"{r.station_id}.csv"
            if bad.exists():
                bad.unlink()
            rec = fetch(GHCN_BASE + f"by_station/{r.station_id}.csv.gz",
                        OUT_DIR / f"{r.station_id}.csv.gz",
                        purpose=f"PRCP {r.station_id} (GHCN-D by_station fallback)")
            method = "GHCN-Daily by_station static file (fallback)"
            n = "see build step" if rec["status"].startswith("ok") else ""
        manifest.append({"station_id": r.station_id, "category": r.category, "request_start": s,
                         "request_end": e, "status": rec["status"], "rows": n, "method": method,
                         "bytes": rec.get("bytes", ""), "sha256": rec.get("sha256", ""),
                         "retrieved_utc": rec["time_utc"], "url": rec.get("final_url", ADS_URL),
                         "error": rec.get("error", "")})
        print(f"{r.station_id:12s} {r.category:30s} {rec['status']:24s} rows={n}")
        time.sleep(1)
    pd.DataFrame(manifest).to_csv(PROCESSED / "station_manifest.csv", index=False)
    print("\nDone. Send data/processed/dc_station_candidates.csv and station_manifest.csv to Claude.")


if __name__ == "__main__":
    main()
