# Stake Engine RGS — Reverse Engineer & Spin Simulator

Reverse-engineer and simulate **Stake Engine** RGS (Remote Gaming Server) slot
spins, and get the full math needed to replicate any slot.

## What this actually does

A Stake Engine game does **not** roll a slot live on every spin. All possible
round outcomes are pre-computed offline into *books*, and each round the RGS just
draws a **weighted-random simulation id** from a *lookUpTable* and replays that
book's events. So the weight table + payouts **is the game's entire math model** —
recover/replay that draw and you have replicated the slot.

A Stake game ships these artifacts:

```
library/
  books/          books_<mode>.jsonl(.zst)   one JSON record per pre-computed round
  lookUpTables/   lookUpTable_<mode>_0.csv   the weight distribution the RGS samples
  publish_files/  index.json                 mode map: name, cost, events, weights
```

* **book**: `{"id": <int>, "payoutMultiplier": <int>, "events": [...]}`
* **lookUpTable row**: `<simulation_id>,<weight>,<payoutMultiplier>`
* `payoutMultiplier` is in **book scale ×100** → `true_multiplier = raw / 100`
* a round win = `bet * true_multiplier`; **RTP = E[true_multiplier] / mode.cost**

This tool reads those files, replicates the exact weighted draw the RGS performs,
and computes RTP, house edge, volatility, hit rate, max win, and the full
win-distribution / RTP-contribution breakdown — per mode. It can also work the
other direction: rebuild the weight table and math from **observed spins alone**.

## Requirements

Python 3.8+. **Pure standard library.** Optional only for compressed books:
`pip install zstandard` *or* the `zstd` CLI (uncompressed `.jsonl`/`.csv` need
nothing).

## Quick start — end-to-end proof

```bash
cd tools/stake-rgs-reverse-engineer
python3 rgs_reverse_engineer.py demo
```

The demo builds a real 3-reel slot, **exactly enumerates** its outcome space,
writes it in Stake format, then: (2) reads the exact math, (3) simulates the
weighted RGS draw, (4) **reverse-engineers the same math from the simulated spins
alone**, (5) shows a provably-fair single spin. Theory ≈ simulation ≈ recovered.

## Commands

### `analyze` — exact math from a real game

```bash
python3 rgs_reverse_engineer.py analyze --index path/to/index.json --json spec.json
# single mode without an index.json:
python3 rgs_reverse_engineer.py analyze --lookup lookUpTable_base.csv --cost 1.0
```

Reports RTP / edge, mean multiplier, std-dev (volatility) & coefficient of
variation, hit rate (>0x and ≥1x), max win, and a per-band probability +
RTP-contribution table. `--json` writes a portable math-spec file.

### `simulate` — play N spins through the RGS draw

```bash
python3 rgs_reverse_engineer.py simulate --index index.json --spins 1000000 \
    --seed 42 --out spins.csv
```

Reports the played-out session (total bet/won/net, simulated RTP with 95% CI,
biggest win) and the drift vs theoretical RTP. `--out` writes a per-spin log
(`spin,simulation_id,multiplier,win,running_rtp`).

### `reverse` — rebuild the math from OBSERVED outcomes

The "reverse engineer any slot" path: feed a stream of round multipliers you
recorded from play (CSV/JSON), and it reconstructs the empirical distribution,
estimates RTP / volatility / hit-rate with a 95% confidence interval, and can emit
a **reconstructed lookUpTable + books** that reproduce it.

```bash
python3 rgs_reverse_engineer.py reverse observed.csv --cost 1.0 \
    --out-lookup recovered_lookUpTable.csv --out-books recovered_books.jsonl
```

`observed.csv` may be a plain list of multipliers, or any CSV with a
`multiplier`/`payout` column (e.g. a `simulate --out` log). Estimate accuracy
scales as ~1/√n — feed a lot of rounds.

### `replicate` — clone a game into a portable spec

```bash
python3 rgs_reverse_engineer.py replicate --index index.json --out-dir replica/
```

Writes `index.json`, `math_spec.json`, and regenerated `lookUpTable_*.csv` /
`books_*.jsonl` that reproduce every mode's math — a self-contained, round-trippable
clone of the slot's math.

### `design` — build a weight table to hit target RTP / hit-rate

```bash
python3 rgs_reverse_engineer.py design --paytable "0.5,1,2,5,10,50,200,1000" \
    --rtp 0.96 --hit 0.30 --out-lookup designed_lookUpTable.csv
```

Distributes winning mass across the paytable so that **both** the target RTP and
the target hit-rate are met exactly (the tier tilt is solved by bisection). Reports
if the targets are infeasible for the given paytable.

### `verify` — theoretical vs simulated RTP gate

```bash
python3 rgs_reverse_engineer.py verify --index index.json --spins 2000000 --tol 0.5
```

Exits non-zero if simulated RTP drifts from theory beyond `--tol` percentage
points — a CI-friendly math sign-off check.

## Notes & conventions

* **Scale:** default book scale is ×100 (`--scale`). RTP math is scale-invariant
  because cost and payout share the same scale.
* **Provably-fair draw:** `RgsSimulator.spin_pf(server_seed, client_seed, nonce)`
  reproduces the HMAC-SHA256 → uniform → weighted-index scheme stake-style RGSs use
  to make each spin independently verifiable.
* Multi-mode games (base + bonus buys) are all read from `index.json`; each mode's
  `cost` is its bet multiplier (base = 1.0, a 100× bonus buy = 100.0).
* A **books file alone cannot** give you the true distribution — it lists each
  distinct outcome once; the weights live in the lookUpTable. Use `analyze` /
  `replicate` when you have both, `reverse` when you only have recorded play.

## Files

| File | Purpose |
|------|---------|
| `rgs_reverse_engineer.py` | CLI: `analyze`, `simulate`, `reverse`, `replicate`, `design`, `verify`, `demo` |
| `rgs_core.py` | Loaders, exact math, weighted RGS simulator, reverse inference, weight design |
| `synth_slot.py` | Synthetic reel/paytable slot → Stake-format artifacts → round-trip demo |

## Disclaimer

For math analysis, QA, and reproducing a game's declared math from its own
published artifacts or from outcomes you are authorised to collect. It does not
break RNG, defeat provable-fairness, or predict future spins — a correctly-seeded
RGS draw is not predictable from past outcomes.
