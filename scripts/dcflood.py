"""Reusable functions that read ONLY the final HDF5 database (outputs/dc_flood_db.h5).

    list_contents(h5)                          -> what is stored (events, sources, stations, dates, metadata)
    get_raster(h5, event_id, source, date)     -> one precipitation grid + coordinates + units
    plot_raster(raster, h5)                    -> map with DC outline, stations, legend, title
    get_timeseries(h5, station_id, event_id)   -> one station's daily series around an event
    plot_timeseries(series)                    -> bar chart with time-zone label, units, missing days marked
    coverage_summary(h5)                       -> compact table of data coverage by source and event

Example
    import dcflood as dc
    H5 = "outputs/dc_flood_db.h5"
    dc.list_contents(H5)
    r = dc.get_raster(H5, "E116", "daymet", "2022-08-05")
    dc.plot_raster(r, H5)
"""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

DEFAULT_H5 = Path(__file__).resolve().parents[1] / "outputs" / "dc_flood_db.h5"


def _s(a) -> list[str]:
    """Decode an HDF5 string dataset to a list of Python str."""
    return [x.decode("utf-8") if isinstance(x, bytes) else str(x) for x in np.asarray(a)]


def _table(group: h5py.Group) -> pd.DataFrame:
    cols = json.loads(group.attrs["columns"])
    out = {}
    for c in cols:
        d = group[c]
        out[c] = _s(d[()]) if d.dtype.kind in ("O", "S") else d[()]
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------
def list_contents(h5_path=DEFAULT_H5, verbose: bool = True) -> dict:
    """List events, sources, variables, stations, date ranges and key metadata."""
    with h5py.File(h5_path, "r") as h5:
        events = _table(h5["events"])[["event_id", "begin_est", "end_est", "duration_min", "qc_flags"]]
        sources = []
        if "gridded" in h5:
            for name, g in h5["gridded"].items():
                ev_ids = [k for k in g.keys() if k.startswith("E")]
                all_dates = sorted({d for e in ev_ids for d in _s(g[e]["date"][()])})
                sources.append({"source": name, "type": "gridded raster", "variable": g.attrs.get("variable", ""),
                                "units": g.attrs.get("units", ""), "resolution": g.attrs.get("native_resolution", ""),
                                "events": len(ev_ids),
                                "date_range": f"{all_dates[0]} .. {all_dates[-1]}" if all_dates else ""})
        stations = []
        if "stations" in h5:
            for sid, g in h5["stations"].items():
                dates = _s(g["date"][()])
                vals = g["prcp_mm"][()]
                stations.append({"station_id": sid, "name": g.attrs["name"], "category": g.attrs["category"],
                                 "network": g.attrs["network"], "lat": g.attrs["latitude"],
                                 "lon": g.attrs["longitude"], "stored_period": f"{dates[0]} .. {dates[-1]}",
                                 "days_with_data": int(np.isfinite(vals).sum()), "days_stored": len(vals)})
            sources.append({"source": "stations", "type": "station time series", "variable": "PRCP",
                            "units": h5["stations"].attrs.get("units", ""), "resolution": "point",
                            "events": len(events), "date_range": ""})
        meta = {k: (v if not isinstance(v, bytes) else v.decode()) for k, v in h5.attrs.items()}
        meta.update({f"metadata/{k}": v for k, v in h5["metadata"].attrs.items()})
    out = {"events": events, "sources": pd.DataFrame(sources), "stations": pd.DataFrame(stations),
           "metadata": meta}
    if verbose:
        print(f"File: {h5_path}")
        print(f"Events: {len(events)}  ({events.begin_est.iloc[0]} .. {events.end_est.iloc[-1]})")
        print("\nSources:\n" + out["sources"].to_string(index=False))
        if len(stations):
            print("\nStations:\n" + out["stations"].to_string(index=False))
    return out


# ----------------------------------------------------------------------------
def get_raster(h5_path=DEFAULT_H5, event_id: str = "E116", source: str = "daymet",
               date: str | None = None) -> dict:
    """Return one daily raster for an event.

    date: 'YYYY-MM-DD' (a day stored for that event). If None, the first 'event' day is used.
    Returns dict with values (2-D, NaN = NoData/missing), lat, lon, x, y, units, crs, date, and notes.
    """
    with h5py.File(h5_path, "r") as h5:
        g = h5[f"gridded/{source}"]
        if event_id not in g:
            raise KeyError(f"{event_id} not stored for {source}")
        eg = g[event_id]
        dates, roles = _s(eg["date"][()]), _s(eg["role"][()])
        if date is None:
            date = next((d for d, r in zip(dates, roles) if r == "event"), dates[0])
        if date not in dates:
            raise KeyError(f"{date} not stored for {event_id}; available: {dates}")
        i = dates.index(date)
        fill = float(g.attrs["missing_value_encoding"])
        vals = eg["prcp"][i].astype("float64")
        vals[vals == fill] = np.nan
        return {"values": vals, "lat": g["lat"][()], "lon": g["lon"][()], "x": g["x"][()], "y": g["y"][()],
                "units": g.attrs["units"], "crs": g.attrs["crs_proj4"], "source": source,
                "event_id": event_id, "date": date, "role": roles[i],
                "available": bool(eg["available"][i]),
                "event_window_est": f"{eg.attrs['begin_est']} to {eg.attrs['end_est']}",
                "day_definition": g.attrs["day_definition"]}


def _dc_outline(h5_path):
    with h5py.File(h5_path, "r") as h5:
        if "dc_boundary_geojson" not in h5["metadata"]:
            return []
        gj = json.loads(h5["metadata/dc_boundary_geojson"][()])
    rings = []
    for feat in gj["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            rings.append(np.array(poly[0]))
    return rings


def plot_raster(raster: dict, h5_path=DEFAULT_H5, ax=None, show_stations: bool = True, save: str | None = None):
    """Map a raster from get_raster() with the DC outline, station locations, colour legend and title."""
    import matplotlib.pyplot as plt
    if ax is None:
        fig, ax = plt.subplots(figsize=(7, 6.5))
    else:
        fig = ax.figure
    v = np.ma.masked_invalid(raster["values"])
    mesh = ax.pcolormesh(raster["lon"], raster["lat"], v, shading="nearest", cmap="Blues",
                         vmin=0, vmax=max(1.0, float(np.nanmax(raster["values"])) if v.count() else 1.0))
    cb = fig.colorbar(mesh, ax=ax, shrink=0.85)
    cb.set_label(f"Precipitation ({raster['units']})")
    nodata = ~np.isfinite(raster["values"])
    if nodata.any() and v.count():
        from matplotlib.colors import ListedColormap
        ax.pcolormesh(raster["lon"], raster["lat"], np.ma.masked_where(~nodata, np.ones_like(raster["values"])),
                      shading="nearest", cmap=ListedColormap(["#bdbdbd"]), vmin=0, vmax=1)
        ax.plot([], [], "s", color="#bdbdbd", ms=8,
                label=f"NoData cell ({int(nodata.sum())}; Daymet water mask)")  # legend entry only
    for ring in _dc_outline(h5_path):
        ax.plot(ring[:, 0], ring[:, 1], color="black", lw=1.4, label="DC boundary")
    if show_stations:
        style = {"in_DC/USC": ("s", "crimson", 8, "in-DC COOP gauge (labelled)"),
                 "in_DC/US1": ("o", "#e377c2", 5, "in-DC CoCoRaHS gauge"),
                 "airport/USW": ("^", "darkorange", 8, "airport, outside DC (labelled)")}
        with h5py.File(h5_path, "r") as h5:
            for sid, g in h5.get("stations", {}).items():
                key = ("in_DC/" if g.attrs["category"] == "in_DC" else "airport/") + sid[:3]
                mk, col, ms, lab = style.get(key, ("o", "grey", 5, "other station"))
                lon, lat = g.attrs["longitude"], g.attrs["latitude"]
                if raster["lon"].min() <= lon <= raster["lon"].max() and raster["lat"].min() <= lat <= raster["lat"].max():
                    ax.plot(lon, lat, mk, color=col, ms=ms, mec="white", label=lab)
                    if not sid.startswith("US1"):  # label only the long-record gauges to keep the map readable
                        ax.annotate(sid, (lon, lat), xytext=(5, 4), textcoords="offset points", fontsize=7)
    h, lab = ax.get_legend_handles_labels()
    uniq = dict(zip(lab, h))
    if uniq:
        ax.legend(uniq.values(), uniq.keys(), loc="lower left", fontsize=7)
    if not v.count():
        ax.text(0.5, 0.5, "No data available for this day", transform=ax.transAxes, ha="center")
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_aspect(1 / np.cos(np.deg2rad(38.9)))
    ax.set_title(f"{raster['source'].capitalize()} daily precipitation, day {raster['date']} ({raster['role']})\n"
                 f"Flood event {raster['event_id']}: {raster['event_window_est']}\n"
                 f"Native 1 km grid, Lambert Conformal Conic; day = midnight–midnight EST", fontsize=9)
    if save:
        fig.savefig(save, dpi=150, bbox_inches="tight")
    return ax


# ----------------------------------------------------------------------------
def get_timeseries(h5_path=DEFAULT_H5, station_id: str = "", event_id: str = "E116",
                   pad_days: int = 3, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Return a station's daily precipitation around an event (or between start/end dates).

    Missing observations are NaN with missing=True; a measured 0.0 is kept as 0.0.
    """
    with h5py.File(h5_path, "r") as h5:
        ev = _table(h5["events"]).set_index("event_id")
        g = h5[f"stations/{station_id}"]
        df = pd.DataFrame({"date": pd.to_datetime(_s(g["date"][()])), "prcp_mm": g["prcp_mm"][()],
                           "mflag": _s(g["mflag"][()]), "qflag": _s(g["qflag"][()]),
                           "obs_time": _s(g["obs_time"][()])})
        attrs = dict(g.attrs)
    if start is None or end is None:
        b = pd.Timestamp(ev.loc[event_id, "begin_date"])
        e = pd.Timestamp(ev.loc[event_id, "end_date"])
        start, end = b - pd.Timedelta(days=pad_days), e + pd.Timedelta(days=pad_days)
    win = pd.DataFrame({"date": pd.date_range(start, end, freq="D")}).merge(df, on="date", how="left")
    win["missing"] = win.prcp_mm.isna()
    win["qc_failed"] = win.qflag.fillna("").str.strip() != ""
    win.attrs.update(station_id=station_id, event_id=event_id, units="mm (daily total)",
                     time_label="Observation date (local standard time, EST = UTC−5)",
                     name=attrs["name"], category=attrs["category"], network=attrs["network"],
                     event_window_est=(f"{ev.loc[event_id, 'begin_est']} to {ev.loc[event_id, 'end_est']}"
                                       if event_id in ev.index else ""))
    return win


def plot_timeseries(series: pd.DataFrame, ax=None, save: str | None = None):
    """Bar chart of daily totals; missing days are hatched grey, the event days shaded."""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    a = series.attrs
    if ax is None:
        fig, ax = plt.subplots(figsize=(8, 4))
    else:
        fig = ax.figure
    ok = ~series.missing
    ax.bar(series.date[ok], series.prcp_mm[ok], width=0.8, color="steelblue", label="daily total", zorder=3)
    zero = ok & (series.prcp_mm == 0)
    if zero.any():
        ax.plot(series.date[zero], [0] * int(zero.sum()), "o", color="steelblue", ms=4, clip_on=False,
                zorder=4, label="measured 0 mm")
    ymax = max(5.0, float(series.prcp_mm.max()) * 1.15 if ok.any() else 5.0)
    for d in series.date[series.missing]:
        ax.axvspan(d - pd.Timedelta(hours=10), d + pd.Timedelta(hours=10), color="lightgrey",
                   hatch="//", alpha=0.6, label="missing (no observation)")
    for d in series.date[ok & series.qc_failed]:
        ax.plot(d, series.loc[series.date == d, "prcp_mm"].iloc[0], "rx", label="failed NOAA QC")
    if a.get("event_window_est"):
        b, e = [pd.Timestamp(t[:10]) for t in a["event_window_est"].split(" to ")]
        ax.axvspan(b - pd.Timedelta(hours=12), e + pd.Timedelta(hours=12), color="gold", alpha=0.25,
                   label="flood event day(s)")
    h, lab = ax.get_legend_handles_labels()
    uniq = dict(zip(lab, h))
    ax.legend(uniq.values(), uniq.keys(), fontsize=8, loc="upper left")
    ax.set_ylim(0, ymax)
    ax.set_ylabel("Daily precipitation total (mm)")
    ax.set_xlabel(a.get("time_label", "Date"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%Y"))
    ax.set_title(f"{a.get('station_id')} {a.get('name')} ({a.get('category')}, {a.get('network')})\n"
                 f"Event {a.get('event_id')}: {a.get('event_window_est')}", fontsize=9)
    if save:
        fig.savefig(save, dpi=150, bbox_inches="tight")
    return ax


# ----------------------------------------------------------------------------
def coverage_summary(h5_path=DEFAULT_H5, by: str = "source") -> pd.DataFrame:
    """Coverage of requested days. by='source' (one row per source) or 'event' (source x event)."""
    with h5py.File(h5_path, "r") as h5:
        cov = _table(h5["metadata/coverage"])
    ev_days = cov[cov.role == "event"]
    if by == "event":
        t = ev_days.groupby(["source", "event_id"]).agg(event_days=("date", "size"),
                                                         days_with_data=("available", "sum")).reset_index()
        t["missing_days"] = t.event_days - t.days_with_data
        return t
    t = ev_days.groupby("source").agg(event_days_requested=("date", "size"),
                                      event_days_with_data=("available", "sum")).reset_index()
    in_rec = ev_days[ev_days.status != "outside_station_record"]
    t["event_days_in_record_period"] = in_rec.groupby("source").size().reindex(t.source).fillna(0).astype(int).to_numpy()
    per_event = ev_days.groupby(["source", "event_id"]).available.any().groupby("source")
    t["events_with_any_data"] = per_event.sum().reindex(t.source).to_numpy()
    t["events_total"] = per_event.size().reindex(t.source).to_numpy()
    t["pct_event_days_covered"] = (100 * t.event_days_with_data / t.event_days_requested).round(1)
    t["pct_within_record_period"] = (100 * t.event_days_with_data
                                     / t.event_days_in_record_period.replace(0, np.nan)).round(1)
    return t
