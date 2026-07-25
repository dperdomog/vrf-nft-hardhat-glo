# Minimal Stake Engine Slot Game (`0_0_minimal`)

A learning game built from scratch on the [Stake Engine Math SDK](https://github.com/StakeEngine/math-sdk).
It is the smallest *complete* slot that still exercises the full pipeline:

```
config (design)  ->  simulate (create_books)  ->  optimize (Rust)  ->  verify  ->  publish_files/
```

## What the game is

- **Board:** 5 reels × 3 rows, **scatter / pay-anywhere** (a symbol pays on *count*, not lines).
- **Symbols:** `H1 H2` (high), `L1 L2` (low), `X` (blank filler — never pays, creates losses),
  `S` (scatter — never pays, only triggers the feature).
- **Free-spins feature:** land **3+ scatters** → 8–15 free spins. During free spins a **global
  multiplier starts at 1× and grows +1 every spin**. More scatters mid-feature retrigger more spins.

## Files (this is all the game-specific code)

| File | Role |
|------|------|
| `game_config.py`       | The rulebook — board size, paytable, reels, triggers, bet mode & distributions |
| `gamestate.py`         | The heart — `run_spin` (base round) + `run_freespin` (the feature loop) |
| `game_executables.py`  | Action helper — evaluate the board and update the wallet |
| `game_calculations.py` | Custom-math layer (empty here; we reuse the framework) |
| `game_override.py`     | Per-round / per-feature state resets |
| `game_optimization.py` | Optimizer targets (RTP contribution + frequency per criteria) |
| `reels/BR0.csv`        | The reel strip (5 columns = 5 reels); symbol frequencies set the volatility |

## How to run it

These files depend on the SDK framework, so they must live *inside* a math-sdk checkout:

```sh
# 1. Get the SDK (needs Python 3.12+ and Rust/Cargo)
git clone https://github.com/StakeEngine/math-sdk.git
cd math-sdk

# 2. Set up the venv (or: make setup)
python3.12 -m venv env
./env/bin/pip install numpy zstandard xlsxwriter python-dotenv toml matplotlib pytest
./env/bin/pip install -e . --no-deps

# 3. Drop this game into the SDK
cp -r /path/to/stake-engine-slot-minimal/0_0_minimal games/0_0_minimal
cp    /path/to/stake-engine-slot-minimal/drive_minimal.py .

# 4. Run it
NSIMS=50000 DO_OPT=1 ./env/bin/python drive_minimal.py
```

Outputs land in `games/0_0_minimal/library/publish_files/`:
`books_base.jsonl.zst`, `lookUpTable_base_0.csv`, `index.json` — the trio you upload to Stake Engine.

`drive_minimal.py` knobs (env vars): `NSIMS` (sims per mode), `DO_OPT=1` (run the Rust optimizer).

## How the optimizer targets work (learned the hard way)

In `game_optimization.py`, each criteria has:
- `rtp`  = that criteria's **contribution** to total RTP
- `hr`   = its **frequency** (1-in-`hr` spins)
- `avg_win = hr × rtp` = the payout **when it occurs** (what the optimizer's "fence" targets)

The per-criteria `rtp` values **must sum to the bet-mode `rtp`** (a hard `verify` check).

## Known limitation (an honest, real lesson)

This game's optimizer currently converges to **RTP ≈ 0.84**, not the 0.95 target. That is *not* a bug —
it's what happens with multi-fence optimization on a small pool:

1. The **base fence under-delivers** — the small pay-anywhere wins can't fill its target fence.
2. The **feature fence hits a ceiling (~0.75)** under the optimizer's mean-to-median / `pmb_rtp` / `wincap`
   constraints at a 1-in-100 trigger rate.
3. `verify` forces base + feature = 0.95, so you can't just crank one lever.

**To reach 0.95** you co-design the pool, not just tweak one number: raise the trigger frequency
(e.g. 1-in-50), widen the feature's win range, co-design the base paytable so its fence is fillable,
and run more sims (500k+) so rare anchor books exist. Single-criteria versions of this game hit RTP
*exactly* — the difficulty is entirely in balancing multiple fences.

Built as a hands-on learning exercise for developing slot math on Stake Engine.
