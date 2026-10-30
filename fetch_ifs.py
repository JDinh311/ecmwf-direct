#!/usr/bin/env python3
"""ECMWF IFS ENS (open data) -> city daily temps -> weighted HDD/CDD JSON.

For one forecast run (00Z or 12Z):
  1. wait until the full 360 h run is published,
  2. download ONLY 2 m temperature for the 50 perturbed members (the open data has no control for 2t) at
     6-hourly steps (ECMWF open data, AWS mirror first, ecmwf.int as fallback),
  3. interpolate the 0.25 deg grid to each city in cities.csv,
  4. build local-calendar-day temperatures per member ((max+min)/2 of the
     6-hourly samples), HDD/CDD per member (base 65 F), then average members,
  5. write data/runs/<YYYYMMDDHH>.json, data/latest_<HH>z.json, data/index.json.

Nothing is written unless the whole run downloaded and decoded cleanly.
"""
import argparse
import csv
import json
import math
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import requests

MODEL_LABEL = "IFS-ENS-direct"
BASE_F = 65.0            # HDD/CDD base, same as the v3.0 engine
STEP_H = 6               # sampling interval used for daily values
MAX_STEP = 360           # last forecast step (hours)
DAILY_METHOD = "minmax"  # "minmax" = (max+min)/2 of samples, "mean" = average of samples
# Download sources, tried in order. Live: AWS first (fastest at release time), then ECMWF, then Google.
# Backfill passes --sources google,azure: AWS throttles archived dates ("SlowDown") and ECMWF's own
# server keeps only the last few days.
SOURCES = ["aws", "ecmwf", "google"]
BASES = {
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "ecmwf": "https://data.ecmwf.int/forecasts",
    "google": "https://storage.googleapis.com/ecmwf-open-data",
    "azure": "https://ai4edataeuwest.blob.core.windows.net/ecmwf",
}
N_MEMBERS = 50   # open data holds the 50 perturbed members for 2t (no control, cf)
MIN_AGE_H = 7.0          # a run is considered "due" this many hours after init


# --------------------------------------------------------------------------- run selection / waiting
def utcnow():
    return datetime.now(timezone.utc)


def index_url(base, init, step=MAX_STEP):
    d, c = init.strftime("%Y%m%d"), init.strftime("%H")
    return f"{base}/{d}/{c}z/ifs/0p25/enfo/{d}{c}0000-{step}h-enfo-ef.index"


def latest_due_init(now):
    """Most recent 00Z/12Z init that is at least MIN_AGE_H old."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for back in range(0, 4):
        for hh in (12, 0):
            init = day - timedelta(days=back) + timedelta(hours=hh)
            if now - init >= timedelta(hours=MIN_AGE_H):
                return init
    raise RuntimeError("no due run found")


def parse_http_date(s):
    try:
        return datetime.strptime(s, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def wait_for_run(init, wait_minutes, poll_s=60, sleep=time.sleep, head=requests.head):
    """Return (source, available_utc) once the final-step index exists; None on timeout."""
    deadline = utcnow() + timedelta(minutes=wait_minutes)
    while True:
        for src in SOURCES:
            try:
                r = head(index_url(BASES[src], init), timeout=20, allow_redirects=True)
            except requests.RequestException as e:
                print(f"  head error {src}: {e}", flush=True)
                continue
            if r.status_code == 200:
                avail = parse_http_date(r.headers.get("Last-Modified", ""))
                print(f"  {src}: final step present (Last-Modified {avail})", flush=True)
                return src, avail
        if utcnow() >= deadline:
            return None
        print("  run not complete yet, waiting...", flush=True)
        sleep(poll_s)


# --------------------------------------------------------------------------- cities
def read_cities(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if not (r.get("city") or "").strip():
                continue
            rows.append({
                "city": r["city"].strip(),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "region": (r.get("region") or "ALL").strip(),
                "weight": float(r["weight"]) if (r.get("weight") or "").strip() else 1.0,
            })
            # optional separate CDD weight (summer set); blank/missing column = same as HDD weight
            cw = (r.get("cdd_weight") or "").strip()
            rows[-1]["cweight"] = float(cw) if cw else rows[-1]["weight"]
    if not rows:
        raise SystemExit("cities.csv has no cities. Fill it in first (city,lat,lon,region,weight).")
    names = [r["city"] for r in rows]
    if len(set(names)) != len(names):
        raise SystemExit("cities.csv has duplicate city names.")
    tot = sum(r["weight"] for r in rows)
    ctot = sum(r["cweight"] for r in rows)
    if ctot <= 0:
        raise SystemExit("cities.csv: cdd_weight values add up to zero.")
    for r in rows:
        r["w"] = r["weight"] / tot
        r["wc"] = r["cweight"] / ctot
    from timezonefinder import TimezoneFinder
    tf = TimezoneFinder()
    for r in rows:
        r["tz"] = tf.timezone_at(lat=r["lat"], lng=r["lon"]) or "UTC"
    return rows


# --------------------------------------------------------------------------- grid interpolation
class GridInterp:
    """Bilinear interpolation weights from a regular lat/lon GRIB grid to points."""

    def __init__(self, ni, nj, lat0, lon0, di, dj, j_pos, lats, lons):
        lats = np.asarray(lats, float)
        lons = np.asarray(lons, float)
        jf = (lats - lat0) / dj if j_pos else (lat0 - lats) / dj
        jf = np.clip(jf, 0, nj - 1 - 1e-9)
        j0 = np.floor(jf).astype(int)
        fj = jf - j0
        j1 = np.minimum(j0 + 1, nj - 1)
        iff = ((lons - lon0) % 360.0) / di
        i0 = np.floor(iff).astype(int)
        fi = iff - i0
        i0 %= ni
        i1 = (i0 + 1) % ni
        self.idx = np.stack([j0 * ni + i0, j0 * ni + i1, j1 * ni + i0, j1 * ni + i1])
        self.w = np.stack([(1 - fj) * (1 - fi), (1 - fj) * fi, fj * (1 - fi), fj * fi])

    def apply(self, values):
        return (values[self.idx] * self.w).sum(axis=0)


def k_to_f(k):
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def decode_file(path, cities, interp_holder):
    """Yield (member, step_h, temps_F[ncity]) for every 2t message in a GRIB file."""
    from eccodes import (codes_get, codes_get_values, codes_grib_new_from_file,
                         codes_release)
    lats = [c["lat"] for c in cities]
    lons = [c["lon"] for c in cities]
    with open(path, "rb") as f:
        while True:
            h = codes_grib_new_from_file(f)
            if h is None:
                break
            try:
                if codes_get(h, "shortName") != "2t":
                    continue
                if interp_holder[0] is None:
                    interp_holder[0] = GridInterp(
                        codes_get(h, "Ni"), codes_get(h, "Nj"),
                        codes_get(h, "latitudeOfFirstGridPointInDegrees"),
                        codes_get(h, "longitudeOfFirstGridPointInDegrees"),
                        codes_get(h, "iDirectionIncrementInDegrees"),
                        codes_get(h, "jDirectionIncrementInDegrees"),
                        codes_get(h, "jScansPositively") == 1, lats, lons)
                dtype = codes_get(h, "dataType")
                pnum = int(codes_get(h, "perturbationNumber"))
                if dtype == "cf" or pnum < 1:   # control is not in the open data for 2t; ignore if it appears
                    continue
                member = pnum - 1               # pf 1..50 -> 0..49
                step = int(codes_get(h, "step"))
                vals = codes_get_values(h)
                yield member, step, k_to_f(interp_holder[0].apply(vals))
            finally:
                codes_release(h)


# --------------------------------------------------------------------------- download
def download_step(init, step, tmpdir, tries=3):
    """Download 2t for all members at one step. Returns path of a GRIB file."""
    from ecmwf.opendata import Client
    last = None
    for attempt in range(tries):
        for src in SOURCES:
            target = os.path.join(tmpdir, f"s{step:03d}_{src}.grib2")
            try:
                client = Client(source=src, model="ifs", resol="0p25",
                                maximum_retries=4, retry_after=15)
                client.retrieve(date=init.strftime("%Y%m%d"), time=init.hour,
                                stream="enfo", type="pf", param="2t",
                                step=step, target=target)
                return target
            except Exception as e:  # noqa: BLE001 - we retry on anything
                last = e
                print(f"  step {step} {src} attempt {attempt + 1} failed: {e}", flush=True)
                if os.path.exists(target):
                    os.remove(target)
        time.sleep(10)
    raise RuntimeError(f"step {step} failed after retries: {last}")


def collect_temps(init, cities, workers=6, downloader=download_step, decoder=decode_file):
    steps = list(range(0, MAX_STEP + 1, STEP_H))
    temps = np.full((N_MEMBERS, len(steps), len(cities)), np.nan)
    interp_holder = [None]
    tmp = tempfile.mkdtemp(prefix="ifs_")
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [(s, ex.submit(downloader, init, s, tmp)) for s in steps]
            for s, fut in futs:            # decode serially (eccodes is not thread-safe)
                path = fut.result()
                seen = set()
                for member, step, vals in decoder(path, cities, interp_holder):
                    seen.add(member)
                    if step != s or not (0 <= member < N_MEMBERS):
                        raise RuntimeError(f"unexpected message member={member} step={step} in step {s} file")
                    temps[member, steps.index(step), :] = vals
                os.remove(path)
                if len(seen) != N_MEMBERS:
                    print(f"  WARNING step {s}h file holds {len(seen)} members, "
                          f"missing {sorted(set(range(N_MEMBERS)) - seen)[:10]}", flush=True)
                print(f"  step {s:3d}h done", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    missing = np.argwhere(np.isnan(temps))
    if len(missing):
        m, k, c = missing[0]
        raise RuntimeError(f"{len(missing)} missing values, first: member {m} step {steps[k]}h city {cities[c]['city']}")
    return temps


# --------------------------------------------------------------------------- daily aggregation
def daily_for_city(temps_mk, init, tzname):
    """temps_mk: (members, steps) F for one city. Returns {date: tmean_members[ens]} for COMPLETE local days."""
    tz = ZoneInfo(tzname)
    n_steps = temps_mk.shape[1]
    first_local = (init.astimezone(tz) - timedelta(days=1)).date()
    last_local = ((init + timedelta(hours=MAX_STEP)).astimezone(tz) + timedelta(days=1)).date()
    out = {}
    partial = set()
    d = first_local
    step_td = timedelta(hours=STEP_H)
    while d <= last_local:
        start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)
        nd = d + timedelta(days=1)
        end = datetime(nd.year, nd.month, nd.day, tzinfo=tz).astimezone(timezone.utc)
        kmin = math.ceil((start - init) / step_td)
        kmax = math.ceil((end - init) / step_td) - 1
        if kmin >= 0 and kmax >= kmin:
            if kmax <= n_steps - 1:
                ok = True
            else:
                # Trailing day (far west time zones): the run ends 1 sample (late evening)
                # short of local midnight. Accept 3 of 4 samples; (max+min)/2 is barely affected.
                ok = (kmax == n_steps) and (n_steps - 1 - kmin + 1) >= 3
                if ok:
                    partial.add(d)
            if ok:
                seg = temps_mk[:, kmin:min(kmax, n_steps - 1) + 1]
                if DAILY_METHOD == "mean":
                    out[d] = seg.mean(axis=1)
                else:
                    out[d] = (seg.max(axis=1) + seg.min(axis=1)) / 2.0
        d = nd
    return out, partial


def build_result(init, cities, temps, source, available, now=None):
    per_city = []
    partial_cities = {}
    for c_i, c in enumerate(cities):
        p, part = daily_for_city(temps[:, :, c_i], init, c["tz"])
        per_city.append(p)
        if part:
            partial_cities[c["city"]] = part
    dates = sorted(set.intersection(*[set(p.keys()) for p in per_city]))
    if not dates:
        raise RuntimeError("no complete local days common to all cities")
    n = len(dates)
    warnings = []
    used_partial = sorted({c for c, ds in partial_cities.items() if any(d in ds for d in dates)})
    if used_partial:
        warnings.append("Last day uses 3 of 4 samples for: " + ", ".join(used_partial))
    city_out = {}
    nat = {k: np.zeros(n) for k in ("hdd", "cdd", "hdd_em", "cdd_em")}
    HDD_KEYS = ("hdd", "hdd_em")
    reg = {}
    for c, p in zip(cities, per_city):
        tm = np.stack([p[d] for d in dates], axis=1)              # (members, days)
        hdd_m = np.maximum(0.0, BASE_F - tm)
        cdd_m = np.maximum(0.0, tm - BASE_F)
        t_mean = tm.mean(axis=0)
        vals = {
            "t_mean": t_mean, "t_std": tm.std(axis=0),
            "hdd": hdd_m.mean(axis=0), "cdd": cdd_m.mean(axis=0),
            "hdd_em": np.maximum(0.0, BASE_F - t_mean), "cdd_em": np.maximum(0.0, t_mean - BASE_F),
        }
        city_out[c["city"]] = {
            "region": c["region"], "weight": round(c["w"], 6), "cdd_weight": round(c["wc"], 6),
            "tz": c["tz"],
            **{k: [round(float(x), 2) for x in v] for k, v in vals.items()},
        }
        for k in nat:   # HDD uses the winter weights, CDD the summer weights
            nat[k] += (c["w"] if k in HDD_KEYS else c["wc"]) * vals[k]
        r = reg.setdefault(c["region"], {"w": 0.0, "wc": 0.0, "hdd": np.zeros(n), "cdd": np.zeros(n)})
        r["w"] += c["w"]
        r["wc"] += c["wc"]
        r["hdd"] += c["w"] * vals["hdd"]
        r["cdd"] += c["wc"] * vals["cdd"]
    now = now or utcnow()
    return {
        "schema": 1, "model": MODEL_LABEL, "source": source,
        "init_utc": init.strftime("%Y-%m-%dT%H:%M:%SZ"), "cycle": init.strftime("%H"),
        "members": N_MEMBERS, "horizon_h": MAX_STEP, "step_h": STEP_H,
        "base_f": BASE_F, "daily_method": DAILY_METHOD,
        "available_utc": available.strftime("%Y-%m-%dT%H:%M:%SZ") if available else None,
        "processed_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_cities": len(cities),
        "warnings": warnings,
        "dates": [d.isoformat() for d in dates],
        **{k: [round(float(x), 2) for x in v] for k, v in nat.items()},
        "regions": {name: {"weight": round(r["w"], 6), "cdd_weight": round(r["wc"], 6),
                           "hdd": [round(float(x / r["w"]), 2) for x in r["hdd"]] if r["w"] > 0 else [0.0] * n,
                           "cdd": [round(float(x / r["wc"]), 2) for x in r["cdd"]] if r["wc"] > 0 else [0.0] * n}
                    for name, r in reg.items()},
        "cities": city_out,
    }


# --------------------------------------------------------------------------- output files
def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def save_outputs(result, out_dir):
    init = datetime.strptime(result["init_utc"], "%Y-%m-%dT%H:%M:%SZ")
    stem = init.strftime("%Y%m%d%H")
    write_json_atomic(os.path.join(out_dir, "runs", f"{stem}.json"), result)
    write_json_atomic(os.path.join(out_dir, f"latest_{result['cycle']}z.json"), result)
    idx_path = os.path.join(out_dir, "index.json")
    idx = {"runs": []}
    if os.path.exists(idx_path):
        with open(idx_path) as f:
            idx = json.load(f)
    idx["runs"] = [r for r in idx["runs"] if r["init_utc"] != result["init_utc"]]
    idx["runs"].append({"init_utc": result["init_utc"], "cycle": result["cycle"],
                        "file": f"runs/{stem}.json", "available_utc": result["available_utc"],
                        "processed_utc": result["processed_utc"], "model": result["model"]})
    idx["runs"].sort(key=lambda r: r["init_utc"])
    idx["updated_utc"] = utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_atomic(idx_path, idx)
    return stem


# --------------------------------------------------------------------------- main
def run(args, downloader=download_step, decoder=decode_file, waiter=wait_for_run):
    cities = read_cities(args.cities)
    if args.date:
        init = datetime.strptime(args.date, "%Y%m%d").replace(tzinfo=timezone.utc) + timedelta(hours=int(args.cycle or 0))
    else:
        init = latest_due_init(utcnow())
        if args.cycle:
            raise SystemExit("--cycle needs --date")
    stem = init.strftime("%Y%m%d%H")
    out_file = os.path.join(args.out_dir, "runs", f"{stem}.json")
    if os.path.exists(out_file) and not args.force:
        print(f"{stem} already processed, nothing to do.")
        return 0
    print(f"Run {init:%Y-%m-%d %HZ}: {len(cities)} cities", flush=True)
    got = waiter(init, args.wait_minutes)
    if got is None:
        print("Run not complete within the wait window.", file=sys.stderr)
        return 2
    source, available = got
    time.sleep(args.settle_s)
    t0 = time.time()
    temps = collect_temps(init, cities, workers=args.workers, downloader=downloader, decoder=decoder)
    print(f"Downloaded + decoded in {time.time() - t0:.0f}s", flush=True)
    result = build_result(init, cities, temps, source, available)
    save_outputs(result, args.out_dir)
    print(f"Saved {stem}: {len(result['dates'])} days, HDD sum {sum(result['hdd']):.1f}, CDD sum {sum(result['cdd']):.1f}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", default="cities.csv")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--date", default="", help="YYYYMMDD of the run (blank = latest due run)")
    ap.add_argument("--cycle", default="", help="00 or 12 (needs --date)")
    ap.add_argument("--wait-minutes", type=int, default=45)
    ap.add_argument("--settle-s", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sources", default="", help="comma list, e.g. google,azure (default: aws,ecmwf,google)")
    args = ap.parse_args(argv)
    if args.sources:
        bad = [x for x in args.sources.split(",") if x not in BASES]
        if bad:
            raise SystemExit(f"unknown source(s): {bad}")
        SOURCES[:] = args.sources.split(",")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
