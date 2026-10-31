"""Shared settings and small helpers used by every script in this project.

Keeping paths and constants in one place means every script agrees on where
files live and which region/time conventions are used.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

# ----------------------------------------------------------------------------
# Folder layout (all paths are relative to the project root, so the project
# can be moved or cloned anywhere)
# ----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"              # original inputs, never modified
PROCESSED = ROOT / "data" / "processed"  # small derived tables (committed)
REFERENCE = ROOT / "data" / "reference"  # e.g. DC boundary (committed)
DOWNLOADS = ROOT / "data" / "downloads"  # temporary downloads (NOT committed)
LOGS = ROOT / "logs"                     # run logs / provenance (committed)
OUTPUTS = ROOT / "outputs"               # figures and the final HDF5

EVENT_CSV = RAW / "DC_flood_times.csv"

# ----------------------------------------------------------------------------
# Time conventions
# ----------------------------------------------------------------------------
# The instructor states the event CSV is in Eastern Standard Time (UTC-5)
# year-round, i.e. NO daylight-saving shift in summer. "Etc/GMT+5" is the
# IANA name for a fixed UTC-5 offset (the sign is inverted by POSIX convention).
EVENT_TZ = "Etc/GMT+5"

# Daymet V4 aims for each daily value to cover midnight-to-midnight local
# standard time (for DC: EST). Because station observation-time corrections
# are approximate, we also keep 1 day before and after each event.
DAY_PAD = 1

# ----------------------------------------------------------------------------
# Spatial region
# ----------------------------------------------------------------------------
# Approximate bounding box of Washington, DC in WGS84 degrees.
DC_BBOX = {"south": 38.7916, "north": 38.9955, "west": -77.1198, "east": -76.9094}
# Extra margin added around the DC box when subsetting rasters, so cells that
# straddle the DC border and the immediate surroundings are kept.
BUFFER_KM = 5

# ----------------------------------------------------------------------------
# Daymet (NASA ORNL DAAC) — Daily Surface Weather Data, Version 4 R1
# ----------------------------------------------------------------------------
DAYMET_SHORT_NAME = "Daymet_Daily_V4R1_2129"
DAYMET_COLLECTION_ID = "C2532426483-ORNL_CLOUD"
DAYMET_DOI = "10.3334/ORNLDAAC/2129"
DAYMET_OPENDAP_TEMPLATE = (
    "https://opendap.earthdata.nasa.gov/collections/" + DAYMET_COLLECTION_ID
    + "/granules/Daymet_Daily_V4R1.daymet_v4_daily_na_prcp_{year}.nc"
)
# Lambert Conformal Conic projection used by Daymet (from the Daymet user guide)
DAYMET_PROJ4 = ("+proj=lcc +lat_1=25 +lat_2=60 +lat_0=42.5 +lon_0=-100 "
                "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")


def ensure_dirs() -> None:
    for p in (RAW, PROCESSED, REFERENCE, DOWNLOADS, LOGS, OUTPUTS):
        p.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def append_jsonl(path: Path, record: dict) -> None:
    """Append one JSON record per line (a simple, human-readable run log)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
