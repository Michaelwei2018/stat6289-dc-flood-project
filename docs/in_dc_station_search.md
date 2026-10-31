# Search for precipitation stations physically inside Washington, DC

This is the log for the "independent open-data investigation" requirement. The full candidate
table, that includes every station's coordinates, network, and period of record, is
`data/processed/dc_station_candidates.csv`. This is also stored in the HDF5 file at
`/metadata/dc_station_candidates_csv`.

## Search strategy

1. **Source searched:** NOAA NCEI Global Historical Climatology Network – Daily (GHCN-Daily).
   It combines the NWS Cooperative Observer network (IDs starting `USC`), first-order/airport
   stations (`USW`) and CoCoRaHS volunteer rain gauges (`US1`).
   The CoCoRaHS network is the largest open source of daily rain-gauge readings inside cities.
2. **Station list:** downloaded `ghcnd-stations.txt` (every GHCN-D station with latitude and longitude)
   and `ghcnd-inventory.txt` (which elements each station reported, and in which years).
3. **Rough filter:** kept the stations inside the DC bounding box plus a 0.05° margin
   (133 stations, including some in Maryland and Virginia).
4. **Exact test:** a point-in-polygon test against the official DC boundary
   (US Census Bureau 2023 cartographic boundary file `cb_2023_us_state_500k`, STATEFP = 11).
   A station counts as "in DC" only if its coordinates fall inside that polygon.
5. **Period filter:** kept only stations whose PRCP record overlaps the event period (1996–2025).
6. **Download:** daily PRCP for each remaining station from the NCEI Access Data Service
   (`https://www.ncei.noaa.gov/access/services/data/v1`, `dataset=daily-summaries`,
   `dataTypes=PRCP`, `units=metric`, `includeAttributes=true`).
   For a station where that service returned no rows, the code fell back to NOAA's static
   per-station file `https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_station/<ID>.csv.gz`.
   That file is in tenths of mm, so it was converted to mm.

## What was found

- **26 GHCN-D stations lie inside the DC polygon. 21 of them have PRCP data overlapping 1996–2025.**
- **Long-record in-DC gauges (NWS COOP):** Dalecarlia Reservoir (USC00182325) and
  National Arboretum (USC00186350). Both report from 1948 to the present.
- **CoCoRaHS volunteer gauges (US1DCDC…):** many stations from 2006 onward, including one
  at the White House (US1DCDC0019). Most have short or intermittent records,
  often from a few hundred days to a few thousand days.
- **Historic in-DC stations with no data in 1996–2025:** Washington (USW00093725, 1852–1965),
  Georgetown (USC00189674, 1860–1866), Brightwood (USC00181135, 1944–1956),
  Bolling Field AFB (USW00013710, 1947–1956) and US Soldiers Home (USC00189035, 1948–1977).
  These were found and recorded, but there was nothing to download for the events.
- **Metadata quirk:** GHCN-D lists the state as "MD" for several stations whose coordinates
  are inside DC, for example Dalecarlia Reservoir and National Arboretum. The polygon test,
  not the state code, decided which stations count as in DC.

## Coverage achieved (event days with data, out of 154)

- Dalecarlia Reservoir (USC00182325): 143 (116 of 125 events).
- National Arboretum (USC00186350): 152 (124 of 125 events).
- Best CoCoRaHS gauge (US1DCDC0014, Washington 5.1 NW): 70 (60 events).
- The other 18 CoCoRaHS gauges: 0–29 each.
- US1DCDC0045: the Access Data Service returned no rows. The static-file fallback was downloaded,
  but the station has no PRCP values on or before 2025-07-20, the last requested day.
  Its record starts after the last event.

## Kept separate: airport stations (not in DC)

Reagan National (USW00013743) is in Arlington, Virginia. Dulles (USW00093738) and BWI (USW00093721)
are also outside DC. They were downloaded as reference series and stored with
`category = airport_reference_outside_DC`. They are never counted as in-DC stations.

## Observed variable, time and units

- Variable: `PRCP`, daily total precipitation in mm. Measured 0 mm is kept as 0, and a day
  with no report is stored as missing (NaN).
- Day definition: COOP and CoCoRaHS observers read the gauge once a day. Both in-DC COOP gauges
  report `obs_time` 0800. CoCoRaHS observers read at about 07:00, but the NCEI download does not
  include their observation time. Each value covers the 24 hours ending at that reading and is
  dated on the reading day. So rain from an evening flood on date D is reported on date D+1.
- Quality flags: the GHCN-D measurement, quality and source flags are stored for every value.

## Limitations

- Only daily totals are available from these gauges. None of them can resolve a 1–3 hour flash flood.
- CoCoRaHS coverage depends on volunteers, so many event days have no report at a given gauge.
  The per-event coverage is listed in `reports/data_availability_report.md`.
- Other possible sources were not searched within the time available: sub-daily gauges run by
  USGS or the DC government, and private networks such as Weather Underground, which are not
  open or quality-controlled.
