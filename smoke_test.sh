#!/usr/bin/env bash
# smoke_test.sh — local end-to-end verification.
#
# Exits 0 iff:
#   1. ETL runs cleanly
#   2. Every section JSON parses + has its required top-level keys
#   3. macro.json bundle is valid
#   4. Static server serves index.html and data/macro.json with HTTP 200
#
# Usage:
#   FRED_KEY=<your-key> bash smoke_test.sh

set -euo pipefail
cd "$(dirname "$0")"

PORT=8765
SERVER_PID=""

bold()  { printf '\033[1m%s\033[0m\n' "$1"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$1"; }
fail()  { printf '\033[31m✗\033[0m %s\n' "$1"; cleanup; exit 1; }

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
bold "[1/4] Run ETL"
# ---------------------------------------------------------------------------
python3 -m etl.run_all || fail "etl/run_all.py crashed"
ok "ETL completed"

# ---------------------------------------------------------------------------
bold "[2/4] Validate JSON outputs"
# ---------------------------------------------------------------------------
EXPECTED=(
  "data/growth.json:gdp,ip,caputil"
  "data/inflation.json:cpi,ppi,expectations"
  "data/labor.json:unemployment,nfp,jolts,wages"
  "data/monetary.json:policy_rates,curve,spreads,money,fci"
  "data/external.json:trade,fx"
  "data/markets.json:equity,credit,vol,housing"
  "data/fiscal.json:us"
  "data/risk.json:recession_prob,stress"
  "data/news.json:headlines,sentiment"
  "data/overview.json:healthScore,ticker,regions"
  "data/macro.json:overview,growth,inflation,labor,monetary,external,markets,fiscal,risk"
)

for spec in "${EXPECTED[@]}"; do
  file="${spec%%:*}"
  keys="${spec#*:}"
  [[ -f "$file" ]] || fail "missing $file"
  python3 - "$file" "$keys" <<'PY' || fail "$file failed validation"
import json, sys
path, keys = sys.argv[1], sys.argv[2].split(",")
with open(path) as f:
    data = json.load(f)
missing = [k for k in keys if k not in data]
if missing:
    print(f"  {path} missing keys: {missing}", file=sys.stderr)
    sys.exit(1)
PY
  ok "$file"
done

# ---------------------------------------------------------------------------
bold "[3/4] Mirror data/ -> public/data/"
# ---------------------------------------------------------------------------
mkdir -p public/data
cp -f data/*.json public/data/
ok "mirrored $(ls public/data | wc -l | tr -d ' ') files"

# ---------------------------------------------------------------------------
bold "[4/4] HTTP smoke test"
# ---------------------------------------------------------------------------
python3 -m http.server -d public "$PORT" >/tmp/macro-smoke.log 2>&1 &
SERVER_PID=$!
sleep 1

http_code() {
  curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/$1"
}

[[ "$(http_code '')" == "200" ]] || fail "GET / returned non-200"
ok "GET / -> 200"

[[ "$(http_code 'data/macro.json')" == "200" ]] || fail "GET data/macro.json non-200"
ok "GET data/macro.json -> 200"

# Health-check the bundle: it must include all 9 sections
python3 - <<PY || fail "macro.json is malformed"
import json, urllib.request, sys
d = json.load(urllib.request.urlopen("http://localhost:$PORT/data/macro.json"))
need = {"overview","growth","inflation","labor","monetary",
        "external","markets","fiscal","risk"}
missing = need - set(d)
if missing:
    print(f"missing sections in bundle: {sorted(missing)}", file=sys.stderr)
    sys.exit(1)
print(f"  health-score regime: {d['overview']['healthScore']['regimeLabel']}")
print(f"  ticker items: {len(d['overview']['ticker'])}")
PY

echo
printf '\033[42;30m %s \033[0m\n' "✅ SMOKE TEST PASSED"
echo
echo "Open the demo locally:"
echo "  python3 -m http.server -d public $PORT"
echo "  → http://localhost:$PORT"
