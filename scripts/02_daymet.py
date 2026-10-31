"""Step 2 — Download Daymet V4 R1 daily precipitation for DC on the event days.

How it works (plain words)
  Daymet is a 1 km x 1 km grid of daily precipitation covering North America,
  stored by NASA as one very large file per year (~ 8,000 x 8,000 cells x 365
  days). We do NOT download those whole files. Instead we use NASA's OPeNDAP
  service, which lets us ask for just a small box of rows/columns (DC + a 5 km
  buffer) and just the days we need. Each answer is a small netCDF file.

  1. Log in to NASA Earthdata (free account; you'll be asked once and the
     login is saved in a .netrc/_netrc file in your home folder).
  2. Ask NASA's catalogue (CMR) which yearly prcp files exist.
  3. Download the grid's x/y coordinates once and find the rows/columns that
     cover DC + buffer.
  4. For each year, download the time axis and find the day indices we need.
  5. Download each run of consecutive needed days as one small file.
  6. Record every request (URL, time, size, checksum, success/failure) in
     data/processed/daymet_manifest.csv, and a day-by-day status table in
     data/processed/daymet_day_status.csv.

Run
  python scripts/02_daymet.py --probe   # quick test: login + one small request
  python scripts/02_daymet.py           # everything (re-running skips files already downloaded)
"""
from __future__ import annotations

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd

from common import (BUFFER_KM, DAYMET_OPENDAP_TEMPLATE, DAYMET_PROJ4, DAYMET_SHORT_NAME,
                    DC_BBOX, DOWNLOADS, LOGS, PROCESSED, append_jsonl, ensure_dirs,
                    sha256_of, utc_now_iso)

OUT_DIR = DOWNLOADS / "daymet"
RUN_LOG = LOGS / "daymet_requests.jsonl"
HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"   # netCDF-4 files start with this
NC3_MAGIC = b"CDF"                   # classic netCDF files start with this
_URL_STYLE = {"style": None}         # remembers which OPeNDAP URL style works


# ----------------------------------------------------------------------------
# Login and HTTP helpers
# ----------------------------------------------------------------------------
def get_session():
    import earthaccess
    print("Logging in to NASA Earthdata (enter your Earthdata username/password if asked)...")
    auth = earthaccess.login(persist=True)
    if not getattr(auth, "authenticated", False):
        sys.exit("Earthdata login failed. Check your username/password at https://urs.earthdata.nasa.gov")
    return earthaccess.get_requests_https_session()


def _looks_like_netcdf(path: Path) -> bool:
    with open(path, "rb") as f:
        head = f.read(8)
    return head.startswith(HDF5_MAGIC) or head.startswith(NC3_MAGIC)


def opendap_get(session, granule_url: str, dap4_ce: str, dest: Path,
                purpose: str, retries: int = 3) -> dict:
    """Request a subset from OPeNDAP and save it as a netCDF file.

    dap4_ce is a DAP4 constraint expression, e.g. "/x;/y" or "/prcp[0:1:2][10:1:40][5:1:30]".
    Two URL styles are tried (DAP4 first, then DAP2) and the one that works is reused.
    """
    dap2_ce = ",".join(part.lstrip("/") for part in dap4_ce.split(";"))
    styles = {
        "dap4": f"{granule_url}.dap.nc4?dap4.ce={quote(dap4_ce, safe='/')}",
        "dap2": f"{granule_url}.nc4?{quote(dap2_ce, safe=',')}",
    }
    order = [_URL_STYLE["style"]] if _URL_STYLE["style"] else ["dap4", "dap2"]
    record = {"time_utc": utc_now_iso(), "purpose": purpose, "granule": granule_url,
              "constraint": dap4_ce, "file": str(dest.relative_to(DOWNLOADS.parent.parent))}
    last_err = ""
    for style in order:
        url = styles[style]
        for attempt in range(1, retries + 1):
            try:
                r = session.get(url, timeout=180, allow_redirects=True)
                if r.status_code == 200 and r.content[:8].startswith((HDF5_MAGIC, NC3_MAGIC)):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)
                    _URL_STYLE["style"] = style
                    record.update(status="ok", url=url, http_status=200, attempts=attempt,
                                  bytes=dest.stat().st_size, sha256=sha256_of(dest))
                    append_jsonl(RUN_LOG, record)
                    return record
                snippet = r.text[:200].replace("\n", " ") if r.content else ""
                last_err = f"HTTP {r.status_code} ({style}): {snippet}"
                if r.status_code in (400, 404):   # bad request: retrying the same URL won't help
                    break
            except Exception as exc:  # network hiccup
                last_err = f"{type(exc).__name__} ({style}): {exc}"
            time.sleep(2 * attempt)
    record.update(status="failed", url=styles[order[-1]], error=last_err)
    append_jsonl(RUN_LOG, record)
    return record


# ----------------------------------------------------------------------------
# Catalogue search
# ----------------------------------------------------------------------------
def find_granules(years: list[int]) -> dict[int, str]:
    """Return {year: OPeNDAP URL} for every Daymet NA prcp file that exists."""
    import earthaccess
    found: dict[int, str] = {}
    try:
        results = earthaccess.search_data(short_name=DAYMET_SHORT_NAME,
                                          granule_name="*_na_prcp_*",
                                          temporal=(f"{min(years)}-01-01", f"{max(years)}-12-31"))
        for g in results:
            name = g["umm"]["GranuleUR"]            # e.g. Daymet_Daily_V4R1.daymet_v4_daily_na_prcp_2022.nc
            yr = int(name.rsplit("_", 1)[-1].split(".")[0])
            links = [u["URL"] for u in g["umm"].get("RelatedUrls", [])
                     if "opendap.earthdata.nasa.gov" in u.get("URL", "") and "/granules/" in u["URL"]]
            found[yr] = links[0] if links else DAYMET_OPENDAP_TEMPLATE.format(year=yr)
        print(f"CMR search found {len(found)} NA prcp granules: {sorted(found)}")
    except Exception as exc:
        print(f"CMR search failed ({exc}); falling back to the documented URL pattern.")
        found = {y: DAYMET_OPENDAP_TEMPLATE.format(year=y) for y in years}
    return found


# ----------------------------------------------------------------------------
# Grid / time helpers
# ----------------------------------------------------------------------------
def save_dmr(session, granule_url: str) -> set[str]:
    """Download the dataset's structure description (DMR) and return variable names."""
    dest = LOGS / f"daymet_dmr_{granule_url.rsplit('_', 1)[-1].replace('.nc', '')}.xml"
    names: set[str] = set()
    try:
        r = session.get(granule_url + ".dmr", timeout=120)
        if r.status_code == 200 and r.text.lstrip().startswith("<"):
            dest.write_text(r.text, encoding="utf-8")
            root = ET.fromstring(r.text)
            for el in root:
                if el.get("name") and not el.tag.endswith(("Dimension", "Attribute")):
                    names.add(el.get("name"))
            print(f"Saved dataset description -> {dest.relative_to(LOGS.parent)}; variables: {sorted(names)}")
        else:
            print(f"Could not read DMR (HTTP {r.status_code}); continuing with default variable list.")
    except Exception as exc:
        print(f"Could not read DMR ({exc}); continuing with default variable list.")
    return names


def dc_index_box(x: np.ndarray, y: np.ndarray) -> tuple[int, int, int, int]:
    """Row/column index ranges of Daymet cells covering DC + buffer (inclusive)."""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", DAYMET_PROJ4, always_xy=True)
    lons = np.r_[np.linspace(DC_BBOX["west"], DC_BBOX["east"], 50),
                 np.full(50, DC_BBOX["east"]),
                 np.linspace(DC_BBOX["east"], DC_BBOX["west"], 50),
                 np.full(50, DC_BBOX["west"])]
    lats = np.r_[np.full(50, DC_BBOX["south"]),
                 np.linspace(DC_BBOX["south"], DC_BBOX["north"], 50),
                 np.full(50, DC_BBOX["north"]),
                 np.linspace(DC_BBOX["north"], DC_BBOX["south"], 50)]
    px, py = tr.transform(lons, lats)
    buf = BUFFER_KM * 1000.0
    xmin, xmax, ymin, ymax = px.min() - buf, px.max() + buf, py.min() - buf, py.max() + buf
    ix = np.where((x >= xmin) & (x <= xmax))[0]
    iy = np.where((y >= ymin) & (y <= ymax))[0]
    if ix.size == 0 or iy.size == 0:
        raise RuntimeError("DC box not found in Daymet grid — check projection settings.")
    return int(iy.min()), int(iy.max()), int(ix.min()), int(ix.max())


def read_vars(path: Path, *names: str) -> dict:
    import netCDF4
    out = {}
    with netCDF4.Dataset(path) as ds:
        for n in names:
            v = ds.variables[n]
            out[n] = v[:]
            out[n + "__attrs"] = {a: v.getncattr(a) for a in v.ncattrs()}
    return out


def decode_days(time_vals, attrs) -> list[str]:
    import netCDF4
    dts = netCDF4.num2date(np.asarray(time_vals), attrs["units"], attrs.get("calendar", "standard"))
    return [f"{d.year:04d}-{d.month:02d}-{d.day:02d}" for d in dts]


def runs(indices: list[int]) -> list[tuple[int, int]]:
    """Group sorted integers into consecutive runs: [1,2,3,7,8] -> [(1,3),(7,8)]."""
    out, start, prev = [], None, None
    for i in sorted(set(indices)):
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((start, prev)); start = prev = i
    if start is not None:
        out.append((start, prev))
    return out


# ----------------------------------------------------------------------------
# Main workflow
# ----------------------------------------------------------------------------
def main(probe: bool) -> None:
    ensure_dirs(); OUT_DIR.mkdir(parents=True, exist_ok=True)
    days_path = PROCESSED / "event_days.csv"
    if not days_path.exists():
        sys.exit("Run scripts/01_events.py first.")
    days = pd.read_csv(days_path)
    if probe:
        days = days[days.date_est.isin(["2022-08-04", "2022-08-05", "2022-08-06"])]
    years = sorted({int(d[:4]) for d in days.date_est})

    session = get_session()
    granules = find_granules(years)

    # --- 1. grid coordinates (identical for every year; fetched once) ------
    ref_year = 2022 if 2022 in granules else min(granules)
    ref_url = granules[ref_year]
    var_names = save_dmr(session, ref_url)
    grid_file = OUT_DIR / "daymet_grid_xy.nc4"
    if not grid_file.exists():
        rec = opendap_get(session, ref_url, "/x;/y", grid_file, "grid coordinates")
        if rec["status"] != "ok":
            sys.exit(f"Could not download grid coordinates: {rec.get('error')}\n"
                     "Please send this message to Claude.")
    g = read_vars(grid_file, "x", "y")
    y0, y1, x0, x1 = dc_index_box(np.asarray(g["x"]), np.asarray(g["y"]))
    print(f"Grid: {len(g['y'])} rows x {len(g['x'])} cols. DC+{BUFFER_KM}km box = rows {y0}-{y1}, "
          f"cols {x0}-{x1} ({y1 - y0 + 1} x {x1 - x0 + 1} cells)")

    optional = [v for v in ("time_bnds", "yearday", "lambert_conformal_conic") if v in var_names]

    manifest, status_rows = [], []
    for year in years:
        year_days = days[days.date_est.str.startswith(str(year))]
        if year not in granules:
            for r in year_days.itertuples():
                status_rows.append({"event_id": r.event_id, "date_est": r.date_est, "role": r.role,
                                    "status": "no_daymet_file_for_year", "file": ""})
            print(f"{year}: no Daymet file found in NASA catalogue -> recorded as missing")
            continue
        url = granules[year]

        # --- 2. time axis for this year -----------------------------------
        tfile = OUT_DIR / f"daymet_time_{year}.nc4"
        if not tfile.exists():
            rec = opendap_get(session, url, "/time", tfile, f"time axis {year}")
            if rec["status"] != "ok":
                print(f"{year}: time axis failed: {rec.get('error')}")
                for r in year_days.itertuples():
                    status_rows.append({"event_id": r.event_id, "date_est": r.date_est, "role": r.role,
                                        "status": "request_failed", "file": ""})
                continue
        t = read_vars(tfile, "time")
        day_list = decode_days(t["time"], t["time__attrs"])
        day_to_idx = {d: i for i, d in enumerate(day_list)}

        needed = sorted({day_to_idx[d] for d in year_days.date_est if d in day_to_idx})

        # --- 3. one request per run of consecutive days --------------------
        idx_to_file = {}
        for t0, t1 in runs(needed):
            dest = OUT_DIR / f"daymet_v4_na_prcp_{year}_{day_list[t0]}_{day_list[t1]}_dc.nc4"
            ce = ";".join([f"/prcp[{t0}:1:{t1}][{y0}:1:{y1}][{x0}:1:{x1}]",
                           f"/time[{t0}:1:{t1}]",
                           f"/y[{y0}:1:{y1}]", f"/x[{x0}:1:{x1}]",
                           f"/lat[{y0}:1:{y1}][{x0}:1:{x1}]", f"/lon[{y0}:1:{y1}][{x0}:1:{x1}]"]
                          + ([f"/time_bnds[{t0}:1:{t1}][0:1:1]"] if "time_bnds" in optional else [])
                          + ([f"/yearday[{t0}:1:{t1}]"] if "yearday" in optional else [])
                          + (["/lambert_conformal_conic"] if "lambert_conformal_conic" in optional else []))
            if dest.exists() and _looks_like_netcdf(dest):
                rec = {"status": "ok (already downloaded)", "bytes": dest.stat().st_size,
                       "sha256": sha256_of(dest), "url": "", "time_utc": ""}
            else:
                rec = opendap_get(session, url, ce, dest, f"prcp {day_list[t0]}..{day_list[t1]}")
                time.sleep(1)  # be polite to the server
            manifest.append({"year": year, "first_day": day_list[t0], "last_day": day_list[t1],
                             "time_index_start": t0, "time_index_end": t1,
                             "row_start": y0, "row_end": y1, "col_start": x0, "col_end": x1,
                             "file": dest.name, "status": rec["status"], "bytes": rec.get("bytes", ""),
                             "sha256": rec.get("sha256", ""), "retrieved_utc": rec.get("time_utc", ""),
                             "url": rec.get("url", ""), "error": rec.get("error", "")})
            ok = rec["status"].startswith("ok")
            for i in range(t0, t1 + 1):
                idx_to_file[i] = dest.name if ok else None
            print(f"{year} {day_list[t0]}..{day_list[t1]}: {rec['status']}")

        for r in year_days.itertuples():
            if r.date_est not in day_to_idx:
                st, f = "not_in_daymet_calendar", ""
            else:
                f = idx_to_file.get(day_to_idx[r.date_est])
                st = "downloaded" if f else "request_failed"
            status_rows.append({"event_id": r.event_id, "date_est": r.date_est, "role": r.role,
                                "status": st, "file": f or ""})

    suffix = "_probe" if probe else ""
    pd.DataFrame(manifest).to_csv(PROCESSED / f"daymet_manifest{suffix}.csv", index=False)
    st = pd.DataFrame(status_rows)
    st.to_csv(PROCESSED / f"daymet_day_status{suffix}.csv", index=False)
    print("\nDay status counts:\n" + st.status.value_counts().to_string())

    if probe:
        files = [m["file"] for m in manifest if m["status"].startswith("ok")]
        if files:
            import netCDF4
            with netCDF4.Dataset(OUT_DIR / files[0]) as ds:
                print("\nPROBE RESULT — variables in subset file:", list(ds.variables))
                p = ds.variables["prcp"]
                print("prcp dims:", p.dimensions, "shape:", p.shape)
                print("prcp attributes:", {a: p.getncattr(a) for a in p.ncattrs()})
                arr = np.ma.filled(p[:].astype(float), np.nan)
                print("daily max over box (mm):", np.nanmax(arr, axis=(1, 2)).round(1).tolist())
                print("lat range:", float(ds.variables['lat'][:].min()), float(ds.variables['lat'][:].max()))
                print("lon range:", float(ds.variables['lon'][:].min()), float(ds.variables['lon'][:].max()))
                tv = ds.variables["time"]
                print("time:", tv[:].tolist(), {a: tv.getncattr(a) for a in tv.ncattrs()})
            print("\nPROBE OK — copy everything above and send it to Claude, then run without --probe.")
        else:
            print("\nPROBE FAILED — copy everything above (and logs/daymet_requests.jsonl) and send it to Claude.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="test login and one small download (Aug 2022)")
    main(ap.parse_args().probe)
