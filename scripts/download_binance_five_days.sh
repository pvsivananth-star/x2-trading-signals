#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

symbol="BTCUSDT"
start_date="2026-11-20"
end_date="2026-11-24"
base_url="https://data.binance.vision/data/spot/daily/trades"
output_dir="data/fixtures/binance/${symbol}/5-day-2026-11-20-to-2026-11-24/raw"
mkdir -p "$output_dir"

python3 - "$start_date" "$end_date" "$symbol" "$base_url" "$output_dir" <<'PY'
import hashlib
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

start = date.fromisoformat(sys.argv[1])
end = date.fromisoformat(sys.argv[2])
symbol, base_url, output = sys.argv[3], sys.argv[4], Path(sys.argv[5])

for day in (start + timedelta(days=i) for i in range((end-start).days+1)):
    name = f"{symbol}-trades-{day}.zip"
    url = f"{base_url}/{symbol}/{name}"
    target = output / name
    if target.exists():
        print(f"exists {target}")
        continue
    request = Request(url, headers={"User-Agent": "x2-trading-signals/1.0"})
    with urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError(f"download failed: {response.status} {url}")
        with target.open("wb") as destination:
            while chunk := response.read(1024*1024):
                destination.write(chunk)
    print(f"downloaded {target}")

manifest = output.parent / "SHA256SUMS"
manifest.write_text("".join(
    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
    for path in sorted(output.glob("*.zip"))
), encoding="utf-8")
PY
