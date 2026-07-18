#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Reconstruct the math model of a REAL Stake Engine game — Valkyrie's
# "Waylanders Forge" — from its PUBLISHED specifications, then run the full
# RGS analysis/simulation against the reconstructed artifacts.
#
# Why reconstruct instead of load the real files?
#   A live commercial game's proprietary books_*.jsonl / lookUpTable_*.csv are
#   served from Stake's RGS behind auth/geo and are not publicly downloadable.
#   This tool produces EXACT math when handed a game's real `library/` folder;
#   without it, the honest path is to rebuild a spec-matched replica whose
#   headline math equals the published numbers, and analyse that.
#
# Published specs used (see the game page + reviews):
#   RTP            : 97.70%   (house edge 2.30%)
#   Max win        : 80,085x
#   Volatility     : High
#   Grid           : 6 reels x 4 rows, Pay All Ways
#   Bet range      : 0.10 - 750.00
#   Modes          : base game + bonus buys (incl. "F U Spins" @ 2000x)
#
# Usage:  bash examples/waylanders_forge.sh [out_dir]
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-/tmp/waylanders_forge}"
G=100000000000     # weight granularity — high, so the 80,085x tail RTP is exact
mkdir -p "$OUT"

echo "### 1. Reconstruct BASE mode -> exact 97.70% RTP, 80,085x max, high vol"
python3 rgs_reverse_engineer.py design \
  --paytable "0.2,0.5,1,2,5,10,25,50,100,250,500,1000,2500,10000,80085" \
  --rtp 0.977 --hit 0.26 --cost 1.0 --granularity "$G" \
  --out-lookup "$OUT/lookUpTable_base.csv" --out-books "$OUT/books_base.jsonl"

echo "### 2. Reconstruct BONUS BUY mode 'F U Spins' -> cost 2000x, RTP 97.70%"
python3 rgs_reverse_engineer.py design \
  --paytable "10,25,50,100,250,500,1000,2000,5000,20000,80085" \
  --rtp 0.977 --hit 0.90 --cost 2000 --granularity "$G" \
  --out-lookup "$OUT/lookUpTable_fuspins.csv" --out-books "$OUT/books_fuspins.jsonl"

echo "### 3. Assemble a Stake-format index.json"
cat > "$OUT/index.json" <<JSON
{
  "game": "valkyrie-waylanders-forge (spec-matched replica)",
  "modes": [
    {"name": "base",     "cost": 1.0,    "events": "books_base.jsonl",    "weights": "lookUpTable_base.csv"},
    {"name": "fu-spins", "cost": 2000.0, "events": "books_fuspins.jsonl", "weights": "lookUpTable_fuspins.csv"}
  ]
}
JSON

echo "### 4. Exact math for every mode"
python3 rgs_reverse_engineer.py analyze --index "$OUT/index.json"

echo "### 5. Simulate real RGS base spins"
python3 rgs_reverse_engineer.py simulate --index "$OUT/index.json" --mode base \
  --spins 5000000 --seed 80085

echo
echo "NOTE: 'verify' at a tight tolerance will FAIL for this title on purpose —"
echo "the 80,085x tail (48% of RTP) makes the RTP standard error ~5pp even at"
echo "10M spins. Certifying to +/-0.5pp needs ~4.9 BILLION spins. That is a real"
echo "property of the game, surfaced by the tool, not an error."
echo
echo "To run against the ACTUAL game instead of a replica, point --index at its"
echo "real library/publish_files/index.json — the math then comes out exact."
