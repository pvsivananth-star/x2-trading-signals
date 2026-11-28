#!/usr/bin/env bash
# Sequential downloader for the configured 16-symbol Yahoo Finance universe.
set -u

DAYS=59
OUT="../back-test-copyright/free-market-data"
PAUSE_SECONDS=3

SYMBOLS=(
  "SPY" "NVDA" "GLD" "USO" "BTC-USD" "ETH-USD"
  "NIFTYBEES.NS" "RELIANCE.NS" "XIU.TO" "ISF.L"
  "SAP.DE" "ASML.AS" "1306.T" "D05.SI" "BHP.AX" "005930.KS"
)

if [[ ! -d "$OUT" ]]; then
  mkdir -p "$OUT"
fi

MANIFEST="$OUT/manifest.json"
BARS="$OUT/bars.csv"
INSTRUMENTS="$OUT/instruments.csv"

# The downloader supports --append for both an empty directory and an
# existing dataset. It also reconstructs a missing manifest from canonical CSVs.

is_downloaded() {
  local symbol="$1"
  [[ -s "$MANIFEST" ]] || return 1
  python - "$MANIFEST" "$symbol" <<'PY'
import json
import sys
path, symbol = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    manifest = json.load(handle)
downloaded = set(manifest.get("symbols_downloaded", []))
downloaded.update(
    item.get("symbol") for item in manifest.get("coverage", [])
    if isinstance(item, dict)
)
sys.exit(0 if symbol in downloaded else 1)
PY
}

SUCCESS=()
FAILED=()
SKIPPED=()

echo "Sequential market data download: ${#SYMBOLS[@]} symbols"
echo "Days: $DAYS"
echo "Output: $OUT"

for INDEX in "${!SYMBOLS[@]}"; do
  SYMBOL="${SYMBOLS[$INDEX]}"
  echo
  echo "----------------------------------------"
  echo "[$((INDEX + 1))/${#SYMBOLS[@]}] Downloading: $SYMBOL"
  echo "----------------------------------------"

  if is_downloaded "$SYMBOL"; then
    echo "SKIPPED: already downloaded: $SYMBOL"
    SKIPPED+=("$SYMBOL")
    continue
  fi

  CMD=(python -m app.cli download-free-data --symbol "$SYMBOL" --days "$DAYS" --out "$OUT" --append)

  if "${CMD[@]}"; then
    SUCCESS+=("$SYMBOL")
    echo "SUCCESS: $SYMBOL"
  else
    FAILED+=("$SYMBOL")
    echo "FAILED: $SYMBOL"
  fi

  sleep "$PAUSE_SECONDS"
done

echo
echo "========================================"
echo "DOWNLOAD SUMMARY"
echo "========================================"
echo "Successful (${#SUCCESS[@]}):"
printf '  %s\n' "${SUCCESS[@]:-None}"
echo "Skipped (${#SKIPPED[@]}):"
printf '  %s\n' "${SKIPPED[@]:-None}"
echo "Failed (${#FAILED[@]}):"
printf '  %s\n' "${FAILED[@]:-None}"
echo "Dataset: $OUT"

if [[ ${#FAILED[@]} -gt 0 ]]; then
  exit 2
fi
