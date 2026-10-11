# ecmwf-direct

Measures when ECMWF's own open-data ensemble (IFS ENS) files appear, so we can
compare against Open-Meteo's publish times (00Z ~8:07 AM ET, 12Z ~8:11 PM ET).

Phase 1 (this repo now): release-time probe only.
Phase 2 (later, if the lead is 3+ hours): downloader that builds HDD/CDD series for a new, separate Google Sheet.

## Files
- `probe.py` — checks ECMWF and the AWS mirror for step files of recent 00Z/12Z runs; logs first-seen time.
- `.github/workflows/probe.yml` — runs it every 10 minutes (free on a public repo).
- `data/probe_log.csv` — created automatically on the first hit.

## Reading the result after ~7 days
For each run, compare `first_seen_et` of the 0h file (first data) and the 360h file (full Wk2, the one that matters)
against Open-Meteo's times from Feed_Timing_Log.
