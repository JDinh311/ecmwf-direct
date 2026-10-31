"""Download daily prices for each NG futures contract (Yahoo, e.g. NGF25.NYM) into data/prices/contracts/.
Lets the swing backtest hold the right contract across a roll instead of the spliced NG=F series."""
import csv, os, sys, time, requests
from datetime import datetime, timezone
UA = {"User-Agent": "Mozilla/5.0"}
CODES = "FGHJKMNQUVXZ"
OUT = "data/prices/contracts"

def fetch(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&period1=1640995200&period2={int(time.time())}"
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    res = (r.json().get("chart", {}).get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return None, "no data"
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(res["timestamp"]):
        if q["close"][i] is None:
            continue
        rows.append([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]])
    return rows, "ok"

def main():
    y0, y1 = int(sys.argv[1]), int(sys.argv[2])   # contract years, e.g. 2023 2026
    os.makedirs(OUT, exist_ok=True)
    report = []
    for y in range(y0, y1 + 1):
        for c in CODES:
            sym = f"NG{c}{str(y)[2:]}.NYM"
            rows, st = fetch(sym)
            if rows:
                with open(f"{OUT}/NG{c}{str(y)[2:]}.csv", "w", newline="") as f:
                    w = csv.writer(f); w.writerow(["date", "open", "high", "low", "close", "volume"]); w.writerows(rows)
            report.append(f"{sym}\t{st}\t{len(rows or [])}\t{rows[0][0] if rows else ''}\t{rows[-1][0] if rows else ''}")
            print(report[-1], flush=True)
            time.sleep(1)
    open(f"{OUT}/_report.tsv", "w").write("\n".join(report) + "\n")

if __name__ == "__main__":
    main()
