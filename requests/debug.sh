# Test archive layouts for winter 2023-24 (0p4-beta and Feb 2024 0p25) on each mirror
for src in google azure aws; do
  for d in 20231215 20240215; do
    for lay in 0p4-beta 0p25 ifs/0p25; do
      case $src in google) b=https://storage.googleapis.com/ecmwf-open-data;; azure) b=https://ai4edataeuwest.blob.core.windows.net/ecmwf;; aws) b=https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com;; esac
      echo "$src $d $lay $(curl -s -o /dev/null -w '%{http_code}' -I $b/$d/00z/$lay/enfo/${d}000000-360h-enfo-ef.index)"
    done
  done
done
python fetch_ifs.py --date 20231215 --cycle 00 --out-dir data/backfill_debug/out --wait-minutes 0 --settle-s 0 --sources google,azure,aws --force
python fetch_ifs.py --date 20240215 --cycle 00 --out-dir data/backfill_debug/out --wait-minutes 0 --settle-s 0 --sources google,azure,aws --force
