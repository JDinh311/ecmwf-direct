"""ECMWF open-data release-time probe.

For each recent 00Z / 12Z ensemble run, logs when key forecast-step files
FIRST appear on ECMWF's servers. Output: data/probe_log.csv
(first-seen time in UTC and ET, plus the server's Last-Modified header).
No credentials needed. Safe to run repeatedly: each file is logged once.
"""
import csv
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BASES = {
    "ecmwf": "https://data.ecmwf.int/forecasts",
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
}
CYCLES = [0, 12]
# 0h = first file, 144h = end of 3-hourly range, 240h = mid Wk2, 360h = last (full Wk2)
STEPS = [0, 144, 240, 360]
MAX_AGE_H = 20  # only watch runs initialised within this many hours
LOG = "data/probe_log.csv"
FIELDS = ["run_date", "cycle", "step_h", "source", "first_seen_utc",
          "first_seen_et", "last_modified_utc", "http_status"]
ET = ZoneInfo("America/New_York")


def url(base, date, cycle, step):
    return (f"{base}/{date}/{cycle:02d}z/ifs/0p25/enfo/"
            f"{date}{cycle:02d}0000-{step}h-enfo-ef.index")


def load_seen():
    seen = set()
    if os.path.exists(LOG):
        with open(LOG, newline="") as f:
            for r in csv.DictReader(f):
                seen.add((r["run_date"], str(int(r["cycle"])),
                          r["step_h"], r["source"]))
    return seen


def main():
    os.makedirs("data", exist_ok=True)
    now = datetime.now(timezone.utc)
    seen = load_seen()
    rows = []
    for day_offset in (0, 1):
        day = (now - timedelta(days=day_offset)).date()
        date = day.strftime("%Y%m%d")
        for cycle in CYCLES:
            init = datetime(day.year, day.month, day.day, cycle, tzinfo=timezone.utc)
            age_h = (now - init).total_seconds() / 3600
            if age_h < 0 or age_h > MAX_AGE_H:
                continue
            for step in STEPS:
                for source, base in BASES.items():
                    if (date, str(cycle), str(step), source) in seen:
                        continue
                    try:
                        r = requests.head(url(base, date, cycle, step),
                                          timeout=15, allow_redirects=True)
                    except requests.RequestException as e:
                        print("error", source, date, cycle, step, e)
                        continue
                    print(source, date, f"{cycle:02d}z", f"{step}h", r.status_code)
                    if r.status_code != 200:
                        continue
                    lm = r.headers.get("Last-Modified", "")
                    try:
                        lm = datetime.strptime(
                            lm, "%a, %d %b %Y %H:%M:%S %Z"
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        pass
                    rows.append({
                        "run_date": date, "cycle": f"{cycle:02d}", "step_h": step,
                        "source": source,
                        "first_seen_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "first_seen_et": now.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S"),
                        "last_modified_utc": lm,
                        "http_status": r.status_code,
                    })
    if rows:
        new_file = not os.path.exists(LOG)
        with open(LOG, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                w.writeheader()
            w.writerows(rows)
        print(f"logged {len(rows)} new rows")
    else:
        print("nothing new")


if __name__ == "__main__":
    main()
