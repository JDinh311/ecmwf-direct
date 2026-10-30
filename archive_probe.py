"""Archive probe: how far back does the ECMWF open-data mirror on AWS keep IFS ENS runs?
Also tries to download hourly NG futures prices (Yahoo NG=F) for the backtest.

Writes data/archive_probe.json (+ data/prices/NG_F_1h.csv and NG_F_1d.csv if Yahoo answers).
Run from GitHub Actions: "Archive probe" -> Run workflow. Takes ~2-4 minutes.
"""
import csv
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import requests

AWS = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
PATTERNS = {   # ECMWF changed folder names over time; try all
    "ifs/0p25": "{d}/{h}z/ifs/0p25/enfo/{d}{h}0000-{s}h-enfo-ef.index",
    "0p25": "{d}/{h}z/0p25/enfo/{d}{h}0000-{s}h-enfo-ef.index",
    "0p4-beta": "{d}/{h}z/0p4-beta/enfo/{d}{h}0000-{s}h-enfo-ef.index",
}
TARGET_FROM, TARGET_TO = date(2025, 10, 15), date(2026, 4, 5)
UA = {"User-Agent": "Mozilla/5.0 (ecmwf-direct archive probe)"}


def head(url):
    try:
        r = requests.head(url, timeout=20, headers=UA)
        return r.status_code, r.headers.get("Last-Modified", "")
    except requests.RequestException as e:
        return -1, str(e)[:80]


def check(day, hour, step=360):
    d, h = day.strftime("%Y%m%d"), f"{hour:02d}"
    for name, pat in PATTERNS.items():
        code, lm = head(f"{AWS}/" + pat.format(d=d, h=h, s=step))
        if code == 200:
            return {"date": day.isoformat(), "cycle": h, "ok": True, "layout": name, "last_modified": lm}
    return {"date": day.isoformat(), "cycle": h, "ok": False, "layout": "", "last_modified": ""}


def inspect_index(day, hour, layout):
    """Count 2t perturbed members and list the 2t steps present in one run."""
    d, h = day.strftime("%Y%m%d"), f"{hour:02d}"
    pat = PATTERNS[layout]
    out = {"run": f"{d} {h}Z", "layout": layout, "members_at_360h": None, "steps_with_2t": []}
    r = requests.get(f"{AWS}/" + pat.format(d=d, h=h, s=360), timeout=30, headers=UA)
    if r.status_code == 200:
        rows = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        pf = [x for x in rows if x.get("param") == "2t" and x.get("type") == "pf"]
        out["members_at_360h"] = len({x.get("number") for x in pf})
        out["types_for_2t"] = sorted({x.get("type") for x in rows if x.get("param") == "2t"})
    steps = []
    for s in range(0, 361, 6):
        code, _ = head(f"{AWS}/" + pat.format(d=d, h=h, s=s))
        if code == 200:
            steps.append(s)
    out["steps_with_2t"] = steps
    out["n_steps"] = len(steps)
    return out


def yahoo(symbol, interval, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}&includePrePost=true"
    r = requests.get(url, timeout=40, headers=UA)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    j = r.json()["chart"]["result"][0]
    ts = j.get("timestamp") or []
    q = j["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(ts):
        o, hi, lo, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if c is None:
            continue
        rows.append([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), o, hi, lo, c, v])
    return rows, "ok"


def main():
    os.makedirs("data/prices", exist_ok=True)
    res = {"probed_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    # 1) every day in the backtest window, both cycles
    days = [TARGET_FROM + timedelta(days=i) for i in range((TARGET_TO - TARGET_FROM).days + 1)]
    jobs = [(d, h) for d in days for h in (0, 12)]
    # 2) monthly spot checks back to Jan 2023
    spots = []
    y, m = 2023, 1
    while (y, m) <= (2026, 10):
        spots.append(date(y, m, 15)); m += 1
        if m == 13:
            y, m = y + 1, 1
    with ThreadPoolExecutor(16) as ex:
        window = list(ex.map(lambda a: check(*a), jobs))
        monthly = list(ex.map(lambda d: check(d, 0), spots))
    res["window"] = {"from": TARGET_FROM.isoformat(), "to": TARGET_TO.isoformat(), "runs_checked": len(window),
                     "runs_present": sum(r["ok"] for r in window),
                     "missing": [r["date"] + " " + r["cycle"] + "Z" for r in window if not r["ok"]][:200],
                     "layouts": sorted({r["layout"] for r in window if r["ok"]})}
    res["monthly_00z"] = [{"date": r["date"], "ok": r["ok"], "layout": r["layout"]} for r in monthly]
    firsts = [r["date"] for r in monthly if r["ok"]]
    res["earliest_month_found"] = firsts[0] if firsts else None

    # 3) content check on two runs in the window
    res["content"] = []
    for d, h in ((date(2025, 12, 15), 0), (date(2026, 2, 1), 12)):
        hit = next((r for r in window if r["date"] == d.isoformat() and r["cycle"] == f"{h:02d}" and r["ok"]), None)
        if hit:
            try:
                res["content"].append(inspect_index(d, h, hit["layout"]))
            except Exception as e:  # noqa: BLE001
                res["content"].append({"run": f"{d} {h:02d}Z", "error": str(e)[:200]})

    # 4) prices
    res["prices"] = {}
    for interval, rng, fn in (("1h", "730d", "NG_F_1h.csv"), ("1d", "5y", "NG_F_1d.csv")):
        try:
            rows, status = yahoo("NG=F", interval, rng)
        except Exception as e:  # noqa: BLE001
            rows, status = None, str(e)[:120]
        info = {"status": status}
        if rows:
            with open(os.path.join("data/prices", fn), "w", newline="") as f:
                w = csv.writer(f); w.writerow(["time_utc", "open", "high", "low", "close", "volume"]); w.writerows(rows)
            info.update(rows=len(rows), first=rows[0][0], last=rows[-1][0])
        res["prices"][interval] = info

    with open("data/archive_probe.json", "w") as f:
        json.dump(res, f, indent=1)

    w = res["window"]
    print(f"Backtest window {w['from']}..{w['to']}: {w['runs_present']}/{w['runs_checked']} runs present, layouts {w['layouts']}")
    if w["missing"]:
        print("  missing (first 20):", ", ".join(w["missing"][:20]))
    print("Earliest month with a 00Z run:", res["earliest_month_found"])
    for c in res["content"]:
        print("Content check:", c)
    for k, v in res["prices"].items():
        print(f"Yahoo NG=F {k}:", v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
