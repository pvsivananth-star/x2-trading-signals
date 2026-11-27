#!/usr/bin/env bash
# Run the Confluence_v1 backtest on a bars folder and write a summary and trade ledger.
# Usage: bash scripts/run_backtest.sh [data_dir] [interval_seconds] [out_dir]
#   data_dir         default: data/fixtures/synthetic_ci (must contain bars.csv, instruments.csv)
#   interval_seconds default: 300 (60, 300 or 900)
#   out_dir          default: backtest_runs/latest
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DATA_DIR="${1:-data/fixtures/synthetic_ci}"
INTERVAL="${2:-300}"
OUT_DIR="${3:-backtest_runs/latest}"
python3 -c "import yaml" 2>/dev/null || { echo "PyYAML is required: pip install pyyaml" >&2; exit 1; }
python3 - "$DATA_DIR" "$INTERVAL" "$OUT_DIR" <<'PY'
import sys
from app.backtest.dataset_runner import run_dataset, write_outputs
data, interval, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
result = run_dataset(data, interval_seconds=interval)
summary = write_outputs(result, out)
print(f"data: {data}  interval: {interval}s")
for sym, info in result["per_symbol"].items():
    g = info["summary"]["gross"]
    if g["trades"] == 0:
        print(f"  {sym:<10} trades=0")
    else:
        print(f"  {sym:<10} trades={g['trades']:<4} win={g['win_rate']:.0%} "
              f"PF={g['profit_factor']:.2f} gross={g['total_pnl']:.2f} maxDD={g['max_drawdown_pct']:.2f}%")
print(f"summary: {summary}")
print(f"ledger:  {out}/trades.csv")
PY
