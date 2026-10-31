# Environmental observations for Washington, DC flood events

STAT 6289 Computer Environments Project
**Assignment version 3:** gridded precipitation from **Daymet** and **DC precipitation stations**.

This project begins with the professor's list of 125 documented DC flood events (1996–2025). It determines the
daily precipitation that Daymet (a 1 km grid) and rain gauges inside DC recorded around each event, while
documenting what is missing. All the findings are stored in a HDF5 database: `outputs/dc_flood_db.h5`.
Reusable Python functions read the data and plot it directly from that file.

## Results at a glance

| Source | Event days with data (of 154) | Events with any data (of 125) |
|---|---|---|
| Daymet V4 R1, 1 km grid | **154 (100%)** | **125** |
| Dalecarlia Reservoir, USC00182325 (in DC, COOP) | 143 (92.9%) | 116 |
| National Arboretum, USC00186350 (in DC, COOP) | 152 (98.7%) | 124 |
| Best CoCoRaHS gauge, US1DCDC0014 (in DC) | 70 (45.5%) | 60 |
| 18 other in-DC CoCoRaHS gauges | 0–29 each | 0–25 each |
| Reagan National / BWI / Dulles (airports, **outside DC**, reference only) | 154 (100%) each | 125 each |

- **Daymet:** all 404 requested days were downloaded (107 requests, 0 failures). The DC subset is
  36 × 33 cells of 1 km. Two cells are NoData on every day; they are a fixed water mask on the
  Potomac River, not missing data.
- **Stations:** 26 GHCN-Daily stations lie inside the DC boundary, and **21** of them have
  precipitation records overlapping 1996–2025. All 24 station downloads (21 in DC + 3 airports)
  succeeded. Most CoCoRaHS volunteer gauges operated for only part of the period, so the report
  separates "no record in that period" from "missing day within the record."
- **Database:** `outputs/dc_flood_db.h5` is 15 MB.

The full numbers are in [`reports/data_availability_report.md`](reports/data_availability_report.md).

## Repository layout

```
data/raw/DC_flood_times.csv        instructor's event list (unchanged)
data/processed/                    event index, download manifests, station list, coverage tables
data/reference/dc_boundary.geojson DC outline (US Census 2023), used for station selection and maps
scripts/01_events.py               validate the event CSV, assign IDs, add UTC times
scripts/02_daymet.py               download Daymet subsets (NASA Earthdata OPeNDAP)
scripts/03_stations.py             find in-DC rain gauges and download their daily PRCP (NOAA)
scripts/04_build_hdf5.py           build outputs/dc_flood_db.h5
scripts/dcflood.py                 reusable functions that read only the HDF5 file
scripts/05_demo.py                 demonstrate every function on event E116 (2022-08-05)
scripts/06_report.py               write reports/data_availability_report.md
outputs/dc_flood_db.h5             the final database
outputs/figures/                   maps and time-series plots made from the HDF5 file
outputs/demo_output.txt            printed output of the demonstration
reports/data_availability_report.md   availability, missingness, spatial and temporal details
docs/in_dc_station_search.md       search log for stations inside DC
logs/                              event validation report and request logs
environment.yml                    software environment
```

Temporary downloads (`data/downloads/`) and credentials are excluded (see `.gitignore`).
The HDF5 file can be rebuilt from scratch with the steps below.

## How to reproduce

1. Install [Miniforge](https://github.com/conda-forge/miniforge), then create the environment:
   ```
   conda env create -f environment.yml
   conda activate stat6289
   ```
   **Windows 11 note:** "Smart App Control" (智能应用控制) blocks the unsigned compiled files
   inside scientific Python packages. The error is `DLL load failed ... An Application Control
   policy has blocked this file`. It must be turned off for these packages to load.
2. Create a free **NASA Earthdata** account (https://urs.earthdata.nasa.gov). Daymet is served
   through NASA Earthdata, and the first run of step 02 asks for the username and password.
3. From the project folder, run:
   ```
   python scripts/01_events.py      # seconds
   python scripts/02_daymet.py      # ~5–15 min (add --probe for a 1-minute test)
   python scripts/03_stations.py    # ~5–10 min
   python scripts/04_build_hdf5.py  # builds outputs/dc_flood_db.h5
   python scripts/05_demo.py        # figures + demo output (use --event E059 for another event)
   python scripts/06_report.py      # availability report
   ```
   Steps 02 and 03 skip files that are already downloaded, so they can be rerun safely.

Tested on Windows 11 with Miniforge, Python 3.11.

## Data sources

| Source | What was used | Access |
|---|---|---|
| Daymet: Daily Surface Weather Data on a 1-km Grid for North America, **Version 4 R1** (ORNL DAAC), [doi:10.3334/ORNLDAAC/2129](https://doi.org/10.3334/ORNLDAAC/2129) | `prcp`, daily total precipitation, mm/day | NASA CMR search for the yearly files `daymet_v4_daily_na_prcp_<year>.nc`, then OPeNDAP index-range subsets (`opendap.earthdata.nasa.gov`, Earthdata login) |
| NOAA NCEI **GHCN-Daily** | `PRCP` for in-DC COOP and CoCoRaHS gauges, plus DCA, IAD and BWI as reference | Station list and inventory from `ncei.noaa.gov/pub/data/ghcn/daily/`, data from the NCEI Access Data Service (`units=metric`), fallback `by_station/<ID>.csv.gz` |
| US Census Bureau **2023 cartographic boundary** `cb_2023_us_state_500k` | DC polygon (STATEFP 11) | `www2.census.gov/geo/tiger/GENZ2023/shp/` |
| Instructor | `DC_flood_times.csv` (event index) | Blackboard |

Every download is logged with URL, UTC time, file size and SHA-256 checksum. These logs are
`data/processed/daymet_manifest.csv`, `data/processed/station_manifest.csv` and `logs/*_requests.jsonl`.
The manifests are also copied into the HDF5 file under `/metadata`.

## Processing steps

1. **Event index** (`01_events.py`): reads the CSV as text without changing it, and assigns IDs
   E001–E125 in file order. Times are interpreted as **EST, fixed UTC−5 all year**, as the
   instructor stated, and UTC = EST + 5 h. The script flags problems but does not fix them:
   two overlapping events (E075, E112), one event that starts when the previous one ends (E072)
   and one zero-length event (E094). It then lists every EST calendar day each event touches,
   plus ±1 padding day: 404 event-days, 372 distinct days.
2. **Daymet** (`02_daymet.py`):
   - **Spatial subsetting:** the DC bounding box plus a 5 km buffer is converted to the Daymet
     Lambert Conformal Conic projection. Only the matching 36 × 33 block of **native 1 km cells**
     is requested.
   - **Temporal filtering:** only the requested days are downloaded, grouped into runs of
     consecutive days.
   - **No reprojection, resampling, upsampling or unit conversion** is done. Values keep Daymet's
     own units (mm/day) and NoData code (−9999).
3. **Stations** (`03_stations.py`): the DC polygon test described in
   [`docs/in_dc_station_search.md`](docs/in_dc_station_search.md), then a daily PRCP download per station.
   - The Access Data Service already returns mm.
   - The fallback file is in tenths of mm and is divided by 10. This is the only unit conversion
     in the project.
   - Missing dates (no row in the source) are filled with NaN, so missing stays distinct from 0 mm.
4. **HDF5** (`04_build_hdf5.py`): see the next section.
5. **No aggregation** across days or stations is stored. Coverage tables count days with data;
   they do not sum rainfall.

## Temporal and spatial compatibility

- **Event times:** EST, UTC−5, no daylight saving, as stated by the instructor. A summer event
  at 15:58 EST is 16:58 on a DC wall clock (EDT).
- **Daymet day:** Daymet V4 aims for each daily value to cover **midnight to midnight local
  standard time** (EST for DC). The time variable is labeled "24-hour day based on local time",
  and its `time_bnds` cover 00:00–24:00. Daymet does this by shifting station readings taken
  before noon back one day, which is approximate, so ±1 day is stored around every event.
- **Daymet calendar:** 365 days per year. In leap years **Dec 31 is not provided**. None of the
  requested days fall on a leap-year Dec 31.
- **Gauge day:** a COOP or CoCoRaHS value dated D covers the 24 hours ending at the morning
  reading on D. Both in-DC COOP gauges report `obs_time` 0800, and CoCoRaHS observers read at
  about 07:00. So an afternoon or evening storm on D appears on **D+1**. Airports report
  midnight to midnight local standard time (`obs_time` 2400).
  **Printed dates are therefore not joined blindly:** original dates and observation times
  are stored, and the difference is documented.
- **Worked example (demo event E116, 2022-08-05 15:58–18:24 EST):** this event followed E115
  (Aug 4 22:45 – Aug 5 00:45).
  - **Daymet:** the box mean is 28.5 mm on Aug 4 and 12.0 mm on Aug 5.
  - **Reagan National (midnight to midnight):** 23.6 mm on Aug 4 and 0.0 mm on Aug 5.
  - **Dalecarlia (08:00 reading):** 27.9 mm on the report dated Aug 5, which mostly covers E115,
    and 20.6 mm on the report dated Aug 6, which covers the E116 storm.
  - **National Arboretum (08:00 reading):** 29.2 mm and 50.5 mm on those same two report dates.
  - Joining the sources by printed date would wrongly assign E115's rain to E116's day.
    The ±1 padding days make this offset visible.
- **Spatial:** Daymet is a 1 km × 1 km grid in Lambert Conformal Conic (WGS84). A gauge measures
  at one point. A daily 1 km cell average and a point gauge reading are not the same quantity,
  so differences between them are expected.

## HDF5 database structure (`outputs/dc_flood_db.h5`)

```
/                                   file description (attributes)
/events/                            flood-event index, one 1-D dataset per column
    event_id, csv_row, begin_date, begin_time, end_date, end_time   (original text)
    begin_est, end_est, begin_utc, end_utc, begin_utc_epoch_s, end_utc_epoch_s
    duration_min, parse_ok, qc_flags
    original_csv                    exact text of the instructor file + SHA-256
/gridded/daymet/                    attributes: source, DOI, units, CRS (proj4 + file attributes),
                                    resolution, NoData, day definition, calendar, retrieval
    x, y (m, LCC)  lat, lon (degrees, 2-D)       shared grid of the 36 x 33 subset
    E001/ ... E125/                 one group per event
        prcp   [day, y, x] float32  mm/day, -9999 = NoData or not obtained
        date, role                  YYYY-MM-DD; event / pad_before / pad_after
        available, status           was the day obtained? why not?
/stations/                          attributes: source, units, missing encoding, day definitions, flags
    <station_id>/                   one group per gauge; attributes: name, network, category
                                    (in_DC or airport_reference_outside_DC), lat, lon, elevation,
                                    record years, retrieval URL/time/checksum
        date      YYYY-MM-DD (continuous daily axis for the stored period)
        prcp_mm   float32, NaN = no observation, 0.0 = measured zero
        mflag, qflag, sflag, obs_time   GHCN-D flags and observation time
/metadata/                          creation time, software versions
    coverage/                       one row per (source, event, requested day)
    daymet_manifest_csv, station_manifest_csv, dc_station_candidates_csv   provenance
    dc_boundary_geojson             DC outline used by the map function
    events_validation_report
```

**Storage choices**
- Rainfall arrays are **float32**, which keeps 7 significant digits, far more than the 0.1 mm
  precision of the data, at half the size of float64.
- Arrays use **gzip level 4 with the shuffle filter**, which works well on many zeros and
  repeated values.
- Daymet arrays are **chunked one day at a time** `(1, 36, 33)`, so reading one map
  decompresses only that day.
- Station series are 1-D and compressed. Text uses variable-length UTF-8 strings.
- Tables are stored column by column, so any language can read them without a custom type.
- Daymet's original NoData code (−9999) is kept rather than replaced, as the assignment asks.

## Functions (`scripts/dcflood.py`)

| Function | Purpose |
|---|---|
| `list_contents(h5)` | events, sources, variables, stations, date ranges, file metadata |
| `get_raster(h5, event_id, source="daymet", date=None)` | one daily grid with lat/lon, x/y, units, CRS, day role and availability |
| `plot_raster(raster, h5)` | map on lat/lon with the DC boundary, stations, colour bar and an informative title |
| `get_timeseries(h5, station_id, event_id, pad_days=3)` | a station's daily values around an event (or between `start`/`end`) with missing and QC columns |
| `plot_timeseries(series)` | daily bars; time-zone label, units, event days shaded, **missing days hatched**, measured zeros marked |
| `coverage_summary(h5, by="source" or "event")` | compact coverage and missingness table |

```python
import sys; sys.path.insert(0, "scripts")
import dcflood as dc
H5 = "outputs/dc_flood_db.h5"
dc.list_contents(H5)
r = dc.get_raster(H5, "E116", "daymet", "2022-08-05")
dc.plot_raster(r, H5)
s = dc.get_timeseries(H5, "USC00182325", "E116")
dc.plot_timeseries(s)
dc.coverage_summary(H5)
```

`scripts/05_demo.py` runs all six on event **E116 (2022-08-05 15:58–18:24 EST)**, the example
used on the assignment page. The figures are in `outputs/figures/` and the printed output is in
`outputs/demo_output.txt`.

## Limitations

- Both sources are **daily**, so neither can show the 1–3 hour timing of a flash flood. They show
  how wet the event day was, including rain before and after the flood window.
- Daymet is a model-interpolated product built from gauges. In a city with few long-record gauges
  it smooths out small, intense storms.
- Many CoCoRaHS gauges have short or intermittent records. A missing report is not zero rain.
- The two long-record COOP gauges are morning readings, so their dates are offset by about one
  day from the Daymet/EST day for evening events.
- The event CSV has no locations, so every event is treated as DC-wide.

## AI acknowledgment

I used **Claude** to help me on this project:
- **Code:** it helped with the Python scripts in `scripts/` (download, HDF5 build, access and plotting
  functions, report generator).
- **Documentation:** it drafted the `docs/in_dc_station_search.md`, text template of the availability 
report, and the README document.
- **Setup and debugging:** it explained the environment setup and helped debug installation
  problems (Windows Smart App Control, NASA Earthdata login, etc.).

I ran the whole pipeline on my own computer, checked the outputs and figures, and reviewed and
edited the documentation. 
