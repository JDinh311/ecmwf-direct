"""Daily NYMEX NG futures settles, contracts 1-4 (EIA series RNGC1-RNGC4), into data/prices/eia_ng_contracts.csv.
Contract 1 = front month; on each expiry contract 2 becomes contract 1, so the swing backtest can hold
one real contract through a roll instead of the spliced NG=F series."""
import io, sys, requests, pandas as pd
UA = {"User-Agent": "Mozilla/5.0 (research script)"}
frames = []
for k in (1, 2, 3, 4):
    url = f"https://www.eia.gov/dnav/ng/hist_xls/RNGC{k}d.xls"
    r = requests.get(url, headers=UA, timeout=60)
    print(url, r.status_code, len(r.content), flush=True)
    r.raise_for_status()
    df = pd.read_excel(io.BytesIO(r.content), sheet_name="Data 1", skiprows=2)
    df.columns = ["date", f"c{k}"]
    frames.append(df.set_index("date"))
out = pd.concat(frames, axis=1).dropna(how="all").sort_index()
out = out[out.index >= "2015-01-01"]
out.to_csv("data/prices/eia_ng_contracts.csv")
print(out.tail(3)); print(len(out), "rows", out.index[0], out.index[-1])
