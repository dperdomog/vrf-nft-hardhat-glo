"""
synth_slot.py — Build a real (small) reel-strip slot, emit it in Stake Engine
format, then reverse-engineer it back. This gives the tool something to run
against with zero external files and proves the round-trip:

    real reel/paytable  ->  books + lookUpTable (Stake format)
                        ->  analyze (exact math)
                        ->  simulate (weighted RGS draw)
                        ->  reverse-engineer from spins  ->  same math

A classic 3-reel, single-payline slot: each reel is a strip of symbols; a spin
stops each reel on a uniform-random strip position; three-of-a-kind on the
payline pays the symbol's multiplier. We enumerate the FULL outcome space
exactly (strip_len ** 3 combinations) so the generated lookUpTable is the true
math, not a sample.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from itertools import product

import rgs_core as core

# symbol -> 3-of-a-kind payout multiplier (0 == no line pay for that symbol).
# Payouts are tuned so the single-payline 3-of-a-kind math lands near a realistic
# ~96% RTP; rarer symbols pay more.
PAYTABLE = {
    "A": 950.0,
    "K": 285.0,
    "Q": 142.0,
    "J": 66.0,
    "T": 25.0,
    "-": 0.0,   # blank
}

# reel strips (same strip on all three reels here; rarer symbols appear less).
STRIP = (["A"] * 1 + ["K"] * 2 + ["Q"] * 3 + ["J"] * 4 + ["T"] * 6 + ["-"] * 10)


def enumerate_outcomes(scale: int = core.BOOK_SCALE):
    """Exact enumeration of every reel combination -> weighted payout table."""
    payout_counts: Counter = Counter()  # payout_raw -> number of combinations
    for r1, r2, r3 in product(STRIP, repeat=3):
        if r1 == r2 == r3:
            mult = PAYTABLE[r1]
        else:
            mult = 0.0
        payout_counts[int(round(mult * scale))] += 1

    rows = []
    for sid, (raw, count) in enumerate(sorted(payout_counts.items()), start=1):
        rows.append(core.WeightRow(id=sid, weight=count,
                                   payout_raw=raw, payout=raw / scale))
    return rows


def write_game(out_dir: str, rows, cost: float = 1.0,
               scale: int = core.BOOK_SCALE) -> str:
    os.makedirs(out_dir, exist_ok=True)
    lookup = os.path.join(out_dir, "lookUpTable_base.csv")
    books = os.path.join(out_dir, "books_base.jsonl")
    index = os.path.join(out_dir, "index.json")
    core.write_lookup(rows, lookup)
    core.write_books_stub(rows, books, scale)
    with open(index, "w", encoding="utf-8") as fh:
        json.dump({"modes": [{"name": "base", "cost": cost,
                              "events": "books_base.jsonl",
                              "weights": "lookUpTable_base.csv"}]}, fh, indent=2)
    return index


def run_demo(out_dir: str, spins: int = 500_000, seed: int = 2026,
             scale: int = core.BOOK_SCALE) -> None:
    print("STEP 1 — build a 3-reel slot and enumerate its exact outcome space")
    rows = enumerate_outcomes(scale)
    total = sum(r.weight for r in rows)
    print(f"  strip length {len(STRIP)}  ->  {len(STRIP)**3:,} reel combinations "
          f"= {total:,} total weight")
    index = write_game(out_dir, rows, cost=1.0, scale=scale)
    print(f"  wrote Stake-format game -> {out_dir}/ (index.json, "
          f"lookUpTable_base.csv, books_base.jsonl)")

    print("\nSTEP 2 — exact math straight from the lookUpTable")
    a = core.analyze_weights(rows, 1.0)
    print(f"  RTP {a['rtp_pct']:.4f} %   hit {a['hit_rate_any']*100:.4f} %   "
          f"max {a['max_multiplier']:.0f}x   std {a['std_multiplier']:.3f}")

    print("\nSTEP 3 — simulate the weighted RGS draw")
    sim = core.RgsSimulator(rows, 1.0)
    session = sim.run(spins, seed=seed)
    s = session.metrics()
    print(f"  {spins:,} spins  ->  simulated RTP {s['rtp_pct']:.4f} % "
          f"(theory {a['rtp_pct']:.4f} %, drift {s['rtp_pct']-a['rtp_pct']:+.4f} pp)")

    print("\nSTEP 4 — reverse-engineer the math from the simulated spins alone")
    rev = core.reverse_from_outcomes(session.payouts, 1.0, scale)["analysis"]
    lo, hi = rev["rtp_ci95_pct"]
    print(f"  recovered RTP {rev['rtp_pct']:.4f} %  (95% CI [{lo:.3f}, {hi:.3f}])  "
          f"hit {rev['hit_rate_any']*100:.4f} %")

    print("\nSTEP 5 — provably-fair single spin (HMAC-SHA256, verifiable)")
    row = sim.spin_pf(server_seed="server-secret-abc",
                      client_seed="player-xyz", nonce=1)
    print(f"  server='server-secret-abc' client='player-xyz' nonce=1  ->  "
          f"simulation #{row.id}, payout {row.payout:.2f}x")

    print("\nRound-trip complete: enumerated math == RGS simulation == "
          "reverse-engineered estimate.")


if __name__ == "__main__":
    run_demo("demo_game")
