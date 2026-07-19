# Math required for a Stake Engine game to be approved to launch

This is the math package + validation a game must satisfy for Stake's RGS to accept
the upload and for the title to be publishable. Sourced from the Stake Engine
math-sdk (`utils/rgs_verification.py`, `verify_mode_volatility`, `create_stat_sheet`)
— not from memory. Run the gates yourself with:

```bash
python3 rgs_reverse_engineer.py publish-check --index <game>/index.json --wincap <cap>
```

## 1. Required artifacts (per game, per mode)

| File | Purpose |
|------|---------|
| `books_<mode>.jsonl.zst` | zstd-compressed; one record per simulation: `{id, payoutMultiplier, events}` |
| `lookUpTable_<mode>_0.csv` | weight table: `simulation_id,weight,payoutMultiplier` (no header) |
| `index.json` | mode map: each mode's `name`, `cost`, `events`, `weights` |
| `config.json` / `math_config.json` / `config_fe_<game>.json` | RGS + front-end config |
| `books_<mode>.verification.json` | `{payout_hash, file_hash, num_entries}` integrity record |
| PAR / stat sheet | per-mode RTP, hit-rate, volatility, tail metrics (audit evidence) |

## 2. HARD gates — format/integrity (RGS `assert`s these; failure blocks upload)

- **payoutMultiplier** values are `uint64` **integers ≥ 0** (book ×100 scale).
- **Minimum non-zero payout ≥ 10** (i.e. ≥ 0.10× — RGS works in cent increments).
- **All payouts are multiples of 10** (0.10× granularity).
- **weight** values are `uint64` **integers ≥ 0**.
- **Σ weights ≤ MAX(uint64)** (`2^64 − 1`).
- Books are **`.jsonl.zst`** and every record has keys `id`, `payoutMultiplier`, `events`.
- **Books ↔ lookUpTable payout arrays match exactly** (md5 of the payout arrays equal).
- **Max win ≤ declared wincap**.

Any failure here is a real blocker: the RGS refuses the file.

## 3. ADVISORY — volatility / risk limits (SDK `warns`; sets the star / vol class)

These classify the volatility rating (the "3-star" limit set below). Exceeding them
does **not** hard-block; it flags a higher-volatility class that needs explicit math
sign-off. They are calibrated per **base bet** — bonus-buy modes routinely exceed
them, which is expected.

| Metric | 3-star limit | Meaning |
|--------|-------------|---------|
| `rtp` | ≤ 0.967 | Return to player ≤ 96.7% |
| `prob5k` | ≤ 1e-2 | P(win ≥ 5000×) |
| `prob10k` | ≤ 5e-3 | P(win ≥ 10000×) |
| `etl40b` | ≤ 0.9 | RTP contribution from wins ≥ 40× |
| `etl10k` | ≤ 0.8 | RTP contribution from wins ≥ 10000× |
| `cvar` | ≤ 800 | upper-tail CVaR at 99.9% |

## 4. Supporting math evidence (the PAR sheet)

Reported per mode and expected to be internally consistent: RTP, std-dev, skewness,
excess kurtosis, non-zero hit-rate, max-win hit-rate, probability of no win,
probability of returning < bet, median, mean-to-median, and the tail metrics above.
Sign-off practice: theoretical RTP recomputed from the weights **and** an empirical
RTP from ≥ 1e6 spins (≥ 2e7 for sign-off), agreeing within tolerance.

## 5. Compliance gates (separate from math, still required to launch)

RTP disclosure, max-win disclosure, jurisdiction rules, and social-/marketing-language
checks (no guaranteed-win claims, etc.). Handled in the Stake publication checklist,
outside the math package.

## Worked example — Coins and Cauldrons replica

`publish-check --index cnc/index.json --wincap 50000`:

- **All 6 modes pass every HARD gate** → uploadable (payouts integer/×10, min 0.10×,
  weights uint64, Σweights in range, max win = 50,000× ≤ cap).
- **base mode meets the 3-star volatility profile cleanly** (rtp 0.9601, prob5k 2.9e-5,
  etl40b 0.82, cvar 440 — all under limit).
- **Buy modes (2×…500×) exceed the base-calibrated limits** (cvar/etl grow with the
  concentrated tail). That is expected for bonus buys; ship them under a higher
  volatility class or re-tune the top tail (lower the 50,000× hit probability) to fit
  3-star. `design`/`build` can regenerate them to a chosen tail budget.
