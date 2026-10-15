# ecmwf-direct

ECMWF's own open-data ensemble (IFS ENS, 51 members) turned into 28-city HDD/CDD, for the
"ECMWF EPS Direct v1" Google Sheet. Runs are complete at about 3:46 AM / 3:46 PM ET
(measured Oct 11-15, see data/probe_log.csv).

## Files
- `fetch_ifs.py` - waits for a run, downloads only 2 m temperature (6-hourly, 51 members), interpolates to the
  cities, builds local-day temps ((max+min)/2 per member), HDD/CDD base 65 F per member, averages, writes JSON.
- `cities.csv` - `city,lat,lon,region,weight,cdd_weight` (weight = HDD set, cdd_weight = CDD set, 0 for the six winter-only cities; both normalised).
- `requirements.txt` - Python packages.
- `.github/workflows/run_ifs.yml` - the job. Started by the Google Sheet at ~07:45 and ~19:45 UTC
  (GitHub's own cron is only a backup). Re-running an already processed run does nothing.
- `.github/workflows/probe.yml` - the earlier release-time probe (can be disabled).
- `data/runs/<YYYYMMDDHH>.json` - one file per run. `data/latest_00z.json`, `data/latest_12z.json`, `data/index.json`.
- `data/probe_log.csv` - release-time evidence from the probe.

## Manual run
Actions -> "IFS direct run" -> Run workflow. Leave the boxes blank for the latest run. To redo a run:
date = YYYYMMDD, cycle = 00 or 12, force = true.

## Conventions (must match the sheet)
- Day = local calendar day of each city. Day 1 of a 00Z run = the init date. A 12Z run cannot cover the
  early morning of that day for Eastern/Central cities, so its first day is the next one; the sheet borrows D1
  from the same-date 00Z run and says so in the Note column.
- Far-west cities: the 15th day uses 3 of 4 samples (the run ends one sample short of local midnight). Listed in `warnings`.
- Wk2 = init date +7 ... +14. Changes between runs are computed on shared calendar days only.
