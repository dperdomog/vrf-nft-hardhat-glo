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

## Getting the real math out of a slot

"The math" of a Stake Engine game = its `books_*.jsonl(.zst)` + `lookUpTable_*.csv`
+ `index.json`. How exactly you can obtain it depends on the game:

1. **Open-source Stake Engine games — exact, fully legitimate.** The math engine is
   open ([StakeEngine/math-sdk](https://github.com/StakeEngine/math-sdk)). Sample
   games ship their real reel strips, paytables, and game logic; the `library/`
   output is git-ignored only because it is *generated*. Regenerate it and analyse:
   ```bash
   git clone https://github.com/StakeEngine/math-sdk
   cd math-sdk && make setup && make run GAME=0_0_lines   # needs Python 3.12 + Rust
   python3 rgs_reverse_engineer.py analyze \
       --index games/0_0_lines/library/publish_files/index.json
   ```
   This tool has been validated end-to-end against exactly this output: it reads the
   official `index.json` / `lookUpTable_*.csv` unchanged and reports the game's
   declared RTP (e.g. `0_0_lines` → **96.7000%**, both `base` and the 100× `bonus`
   mode, 5000× wincap) to the digit. Real optimised weights are large integers
   (e.g. `99910483480`); the loader handles them natively.

2. **Your own recorded play / replay data — statistical recovery.** For a closed
   commercial game the weight table is not published, but you can reconstruct the
   distribution from a large sample of rounds you are authorised to collect (each
   Stake Engine round is individually replayable), then feed the multipliers to
   `reverse`. Accuracy scales as ~1/√n.

3. **Published specs — reconstruction.** Match headline RTP / max win / volatility
   with `design` when neither the files nor a play sample are available. Lowest
   fidelity, always available. (See `examples/waylanders_forge.sh`.)

What this tool does **not** do: rip a specific studio's proprietary book/weight
files off Stake's RGS/CDN. Even though the client streams one book per bet, the RGS
never hands out the weight table, and extracting/redistributing another studio's
math is an IP/ToS matter. Use open-source math, your own authorised play data, or
published specs.

## Disclaimer

For math analysis, QA, and reproducing a game's declared math from its own
published artifacts or from outcomes you are authorised to collect. It does not
break RNG, defeat provable-fairness, or predict future spins — a correctly-seeded
RGS draw is not predictable from past outcomes.
