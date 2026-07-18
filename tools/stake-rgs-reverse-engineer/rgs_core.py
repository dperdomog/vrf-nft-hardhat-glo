"""
rgs_core.py — Core library for reverse-engineering and simulating Stake Engine
RGS (Remote Gaming Server) slot spins.

A Stake Engine game ships a `library/` of deterministic artifacts:

    library/
      books/          books_<mode>.jsonl(.zst)   one JSON record per simulated round
      lookUpTables/   lookUpTable_<mode>_0.csv   the weight distribution the RGS samples
      publish_files/  index.json                 mode map: name, cost, events, weights

Contract (from the Stake Engine book/RGS spec):
  * A "book" is one round outcome:  {"id": <int>, "payoutMultiplier": <int>, "events": [...]}
  * A lookUpTable row is:           <simulation_id>,<weight>,<payoutMultiplier>
  * `payoutMultiplier` is stored in BOOK scale (x100):  true_multiplier = raw / 100
  * A round's win = bet * true_multiplier ; RTP = E[true_multiplier] / mode.cost

The RGS never rolls symbols live. It draws a weighted-random simulation id from the
lookUpTable and replays that book's pre-computed events. Reproducing that exact weighted
draw + the weight/payout table therefore *replicates the entire slot math*.

Pure standard library. `.zst` books are read via the optional `zstandard` package or a
`zstd` CLI fallback; uncompressed `.jsonl`/`.csv` need nothing extra.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
import os
import random
import subprocess
from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import accumulate
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

BOOK_SCALE = 100  # Stake book/event payout scale: raw integer 100 == 1.00x bet.


# --------------------------------------------------------------------------- #
# File loading                                                                #
# --------------------------------------------------------------------------- #
def _open_text_lines(path: str) -> Iterator[str]:
    """Yield decoded text lines from a .jsonl or .jsonl.zst / .csv(.zst) file."""
    if path.endswith(".zst"):
        yield from _open_zst_lines(path)
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            yield line


def _open_zst_lines(path: str) -> Iterator[str]:
    # Preferred: the `zstandard` python package.
    try:
        import zstandard  # type: ignore

        with open(path, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                text = io.TextIOWrapper(reader, encoding="utf-8")
                for line in text:
                    yield line
        return
    except ImportError:
        pass
    # Fallback: the `zstd` command line tool.
    try:
        proc = subprocess.run(
            ["zstd", "-dc", path], check=True, stdout=subprocess.PIPE
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"Cannot decompress {path!r}. Install the python package "
            f"`pip install zstandard`, or the `zstd` CLI, or point at an "
            f"uncompressed .jsonl file instead.\n  ({exc})"
        ) from exc
    for line in proc.stdout.decode("utf-8").splitlines():
        yield line


@dataclass
class Book:
    id: int
    payout_raw: int           # payoutMultiplier as stored in the book (x100)
    payout: float             # true multiplier (payout_raw / scale)
    n_events: int = 0


def load_books(path: str, scale: int = BOOK_SCALE) -> Dict[int, Book]:
    """Load books_<mode>.jsonl(.zst) into {id: Book}."""
    books: Dict[int, Book] = {}
    for line in _open_text_lines(path):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        bid = int(rec.get("id", rec.get("simulation", len(books) + 1)))
        raw = rec.get("payoutMultiplier", rec.get("payout", 0))
        raw = int(round(float(raw)))
        events = rec.get("events", [])
        books[bid] = Book(id=bid, payout_raw=raw, payout=raw / scale,
                          n_events=len(events) if isinstance(events, list) else 0)
    return books


@dataclass
class WeightRow:
    id: int
    weight: int
    payout_raw: int
    payout: float


def load_lookup(path: str, scale: int = BOOK_SCALE) -> List[WeightRow]:
    """Load lookUpTable_<mode>.csv. Rows: id, weight, payoutMultiplier (no header
    in canonical Stake output, but a header row is tolerated)."""
    rows: List[WeightRow] = []
    for line in _open_text_lines(path):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            sid = int(float(parts[0]))
            weight = int(float(parts[1]))
            payout_raw = int(round(float(parts[2])))
        except ValueError:
            # header row such as "id,weight,payoutMultiplier" -> skip.
            continue
        rows.append(WeightRow(id=sid, weight=weight,
                              payout_raw=payout_raw, payout=payout_raw / scale))
    if not rows:
        raise RuntimeError(f"No usable weight rows parsed from {path!r}.")
    return rows


@dataclass
class Mode:
    name: str
    cost: float                       # bet-cost multiplier for this mode (base = 1.0)
    weights: List[WeightRow] = field(default_factory=list)
    books: Dict[int, Book] = field(default_factory=dict)
    events_path: Optional[str] = None
    weights_path: Optional[str] = None


def _resolve(base_dir: str, ref: str) -> str:
    """Resolve a file path referenced in index.json, trying a few common layouts."""
    if os.path.isabs(ref) and os.path.exists(ref):
        return ref
    cands = [
        os.path.join(base_dir, ref),
        os.path.join(base_dir, "..", ref),
        os.path.join(base_dir, os.path.basename(ref)),
        os.path.join(base_dir, "books", os.path.basename(ref)),
        os.path.join(base_dir, "lookUpTables", os.path.basename(ref)),
        os.path.join(base_dir, "..", "books", os.path.basename(ref)),
        os.path.join(base_dir, "..", "lookUpTables", os.path.basename(ref)),
    ]
    for c in cands:
        if os.path.exists(c):
            return os.path.normpath(c)
    return os.path.normpath(os.path.join(base_dir, ref))


def load_game(index_path: str, scale: int = BOOK_SCALE,
              load_book_events: bool = False) -> List[Mode]:
    """Load every mode declared in an index.json / config.json.

    The weight table is authoritative for math; book events are only loaded when
    `load_book_events` is set (they are large and unnecessary for the math)."""
    with open(index_path, "r", encoding="utf-8") as fh:
        idx = json.load(fh)
    base = os.path.dirname(os.path.abspath(index_path))

    mode_defs = idx.get("modes")
    if mode_defs is None:
        # config.json variants store bet modes under other keys.
        mode_defs = idx.get("betModes") or idx.get("bet_modes") or []

    modes: List[Mode] = []
    for md in mode_defs:
        name = md.get("name") or md.get("mode") or md.get("id") or "base"
        cost = float(md.get("cost", md.get("betCost", md.get("costMultiplier", 1.0))))
        weights_ref = (md.get("weights") or md.get("lookUpTable")
                       or md.get("weightTable") or md.get("weight"))
        events_ref = md.get("events") or md.get("books") or md.get("bookFile")

        mode = Mode(name=name, cost=cost)
        if weights_ref:
            wp = _resolve(base, weights_ref)
            mode.weights_path = wp
            mode.weights = load_lookup(wp, scale)
        if events_ref:
            mode.events_path = _resolve(base, events_ref)
            if load_book_events and os.path.exists(mode.events_path):
                mode.books = load_books(mode.events_path, scale)
        modes.append(mode)
    return modes


def mode_from_files(name: str, cost: float, lookup_path: str,
                    books_path: Optional[str] = None,
                    scale: int = BOOK_SCALE,
                    load_book_events: bool = False) -> Mode:
    """Build a Mode directly from explicit file paths (no index.json)."""
    mode = Mode(name=name, cost=cost, weights_path=lookup_path)
    mode.weights = load_lookup(lookup_path, scale)
    if books_path:
        mode.events_path = books_path
        if load_book_events:
            mode.books = load_books(books_path, scale)
    return mode


# --------------------------------------------------------------------------- #
# Theoretical math (exact, straight from the weight table)                     #
# --------------------------------------------------------------------------- #
# Multiplier buckets used for win-distribution / RTP-contribution breakdowns.
BUCKETS: List[Tuple[str, float, float]] = [
    ("loss (0x)",        0.0,      0.0),
    ("(0x - 1x)",        1e-9,     1.0),
    ("[1x - 2x)",        1.0,      2.0),
    ("[2x - 5x)",        2.0,      5.0),
    ("[5x - 10x)",       5.0,      10.0),
    ("[10x - 20x)",      10.0,     20.0),
    ("[20x - 50x)",      20.0,     50.0),
    ("[50x - 100x)",     50.0,     100.0),
    ("[100x - 500x)",    100.0,    500.0),
    ("[500x - 1000x)",   500.0,    1000.0),
    ("[1000x - 5000x)",  1000.0,   5000.0),
    ("[5000x+ ]",        5000.0,   math.inf),
]


def _bucket_index(mult: float) -> int:
    if mult <= 0:
        return 0
    for i, (_, lo, hi) in enumerate(BUCKETS):
        if i == 0:
            continue
        if lo <= mult < hi:
            return i
    return len(BUCKETS) - 1


def analyze_weights(rows: Sequence[WeightRow], cost: float) -> dict:
    """Exact math from the weight distribution — this IS the game's math model."""
    total_w = sum(r.weight for r in rows)
    if total_w <= 0:
        raise ValueError("Total weight is zero.")

    mean = sum(r.weight * r.payout for r in rows) / total_w          # E[multiplier]
    mean_sq = sum(r.weight * r.payout * r.payout for r in rows) / total_w
    var = max(mean_sq - mean * mean, 0.0)
    std = math.sqrt(var)

    win_w = sum(r.weight for r in rows if r.payout > 0)
    ge1_w = sum(r.weight for r in rows if r.payout >= 1.0)
    max_mult = max((r.payout for r in rows), default=0.0)

    # Bucket breakdown: probability mass and RTP contribution per multiplier band.
    b_w = [0] * len(BUCKETS)
    b_contrib = [0.0] * len(BUCKETS)
    for r in rows:
        bi = _bucket_index(r.payout)
        b_w[bi] += r.weight
        b_contrib[bi] += r.weight * r.payout
    buckets = []
    for i, (label, _, _) in enumerate(BUCKETS):
        if b_w[i] == 0:
            continue
        prob = b_w[i] / total_w
        rtp_c = (b_contrib[i] / total_w) / cost
        buckets.append({
            "band": label,
            "prob": prob,
            "one_in": (1.0 / prob) if prob > 0 else math.inf,
            "rtp_contribution": rtp_c,
        })

    rtp = mean / cost
    return {
        "cost": cost,
        "n_outcomes": len(rows),
        "total_weight": total_w,
        "rtp": rtp,
        "rtp_pct": rtp * 100.0,
        "mean_multiplier": mean,
        "std_multiplier": std,
        "variance": var,
        "coeff_of_variation": (std / mean) if mean > 0 else math.inf,
        "hit_rate_any": win_w / total_w,           # any payout > 0
        "hit_rate_ge1x": ge1_w / total_w,          # payout >= stake
        "hit_one_in_any": (total_w / win_w) if win_w else math.inf,
        "max_multiplier": max_mult,
        "buckets": buckets,
    }


# --------------------------------------------------------------------------- #
# RGS spin simulator — replicates the weighted draw the real RGS performs      #
# --------------------------------------------------------------------------- #
class RgsSimulator:
    """Faithful replica of the RGS selection step: draw a weighted-random
    simulation id from the lookUpTable, serve that book's payout.

    Two randomness sources are supported:
      * `run()`      — a seeded Mersenne-Twister (fast bulk simulation).
      * `spin_pf()`  — provably-fair HMAC-SHA256(server_seed, client:nonce) draw,
                       the scheme stake-style RGSs use to make each spin verifiable.
    """

    def __init__(self, rows: Sequence[WeightRow], cost: float):
        if not rows:
            raise ValueError("No weight rows.")
        self.rows = list(rows)
        self.cost = cost
        self.total_w = sum(r.weight for r in self.rows)
        # cumulative upper bounds for bisect-based O(log n) selection.
        self._cum = list(accumulate(r.weight for r in self.rows))

    def _select(self, draw: int) -> WeightRow:
        """draw in [0, total_w)  ->  the row whose cumulative band contains it."""
        idx = bisect_left(self._cum, draw + 1)
        return self.rows[idx]

    # ---- bulk seeded simulation -------------------------------------------- #
    def run(self, n: int, seed: Optional[int] = None) -> "SpinSession":
        """Store every spin (payout + id). O(n) memory — fine up to a few
        million spins; use `run_stream` for very large n."""
        rng = random.Random(seed)
        payouts: List[float] = []
        ids: List[int] = []
        tw = self.total_w
        for _ in range(n):
            row = self._select(rng.randrange(tw))
            payouts.append(row.payout)
            ids.append(row.id)
        return SpinSession(payouts=payouts, ids=ids, cost=self.cost, n=n)

    # ---- streaming simulation (O(1) memory) -------------------------------- #
    def run_stream(self, n: int, seed: Optional[int] = None,
                   progress_every: int = 0, on_progress=None) -> dict:
        """Simulate n spins accumulating online statistics only (no per-spin
        storage), so hundreds of millions of spins fit in constant memory.
        Returns the same metrics dict shape as SpinSession.metrics()."""
        rng = random.Random(seed)
        randrange = rng.randrange
        bl = bisect_left
        cum = self._cum
        rows = self.rows
        tw = self.total_w
        total = 0.0
        total_sq = 0.0
        hits = 0
        mx = 0.0
        for i in range(1, n + 1):
            p = rows[bl(cum, randrange(tw) + 1)].payout
            total += p
            total_sq += p * p
            if p > 0.0:
                hits += 1
                if p > mx:
                    mx = p
            if progress_every and on_progress and i % progress_every == 0:
                on_progress(i, total, total_sq, hits, mx)
        return _session_metrics(n, self.cost, total, total_sq, hits, mx)

    # ---- provably-fair single spin ----------------------------------------- #
    def spin_pf(self, server_seed: str, client_seed: str, nonce: int) -> WeightRow:
        msg = f"{client_seed}:{nonce}".encode()
        digest = hmac.new(server_seed.encode(), msg, hashlib.sha256).hexdigest()
        # first 52 bits -> uniform float in [0,1) (same idea as stake fairness math)
        frac = int(digest[:13], 16) / float(1 << 52)
        draw = min(int(frac * self.total_w), self.total_w - 1)
        return self._select(draw)


def _session_metrics(n: int, cost: float, total_win: float, total_sq: float,
                     hits: int, mx: float) -> dict:
    """Build the metrics dict from accumulated sums (shared by stored & streamed
    simulation so both report identical fields)."""
    total_bet = n * cost
    mean = total_win / n if n else 0.0
    var = max((total_sq / n - mean * mean) if n else 0.0, 0.0)
    std = math.sqrt(var)
    rtp = mean / cost
    se = (std / math.sqrt(n) / cost) if n else 0.0   # standard error of RTP
    return {
        "spins": n,
        "cost": cost,
        "total_bet": total_bet,
        "total_won": total_win,
        "net": total_win - total_bet,
        "rtp": rtp,
        "rtp_pct": rtp * 100.0,
        "rtp_stderr_pct": se * 100.0,
        "rtp_ci95_pct": (max(rtp - 1.96 * se, 0) * 100.0, (rtp + 1.96 * se) * 100.0),
        "mean_multiplier": mean,
        "std_multiplier": std,
        "hit_rate_any": hits / n if n else 0.0,
        "max_multiplier": mx,
    }


@dataclass
class SpinSession:
    payouts: List[float]
    ids: List[int]
    cost: float
    n: int

    def metrics(self) -> dict:
        total = sum(self.payouts)
        total_sq = sum(p * p for p in self.payouts)
        hits = sum(1 for p in self.payouts if p > 0)
        mx = max(self.payouts) if self.payouts else 0.0
        return _session_metrics(self.n, self.cost, total, total_sq, hits, mx)


# --------------------------------------------------------------------------- #
# Reverse inference — rebuild a weight table from observed outcomes            #
# --------------------------------------------------------------------------- #
def reverse_from_outcomes(multipliers: Sequence[float], cost: float,
                          scale: int = BOOK_SCALE) -> dict:
    """Given only observed round multipliers (recorded play, or a books file with
    no weights), reconstruct the empirical distribution and a lookUpTable that
    replicates it — i.e. reverse-engineer the slot math from samples alone."""
    counts: Dict[int, int] = {}
    for m in multipliers:
        raw = int(round(m * scale))
        counts[raw] = counts.get(raw, 0) + 1

    rows: List[WeightRow] = []
    for sid, (raw, w) in enumerate(sorted(counts.items()), start=1):
        rows.append(WeightRow(id=sid, weight=w, payout_raw=raw, payout=raw / scale))

    n = len(multipliers)
    analysis = analyze_weights(rows, cost)
    # confidence on the empirical RTP given the sample size.
    mean = analysis["mean_multiplier"]
    std = analysis["std_multiplier"]
    se = (std / math.sqrt(n) / cost) if n else 0.0
    analysis["sample_size"] = n
    analysis["rtp_stderr_pct"] = se * 100.0
    analysis["rtp_ci95_pct"] = (max(analysis["rtp"] - 1.96 * se, 0) * 100.0,
                                (analysis["rtp"] + 1.96 * se) * 100.0)
    return {"rows": rows, "analysis": analysis}


# --------------------------------------------------------------------------- #
# Forward design — build a weight table that hits target RTP / hit-rate        #
# --------------------------------------------------------------------------- #
def design_weights(paytable: Sequence[float], target_rtp: float,
                   target_hit_rate: float, cost: float = 1.0,
                   decay: float = 0.35, scale: int = BOOK_SCALE,
                   granularity: int = 10_000_000) -> Tuple[List[WeightRow], dict]:
    """Construct a lookUpTable that reproduces a target math profile, honouring
    BOTH the target RTP and the target hit-rate exactly.

    `paytable` is the set of winning multipliers the game can award. Winning
    probability mass always sums to `target_hit_rate`; it is distributed across
    tiers as prob_k proportional to base**k, and `base` is solved by bisection so
    that the exact RTP equals `target_rtp` (bigger `base` -> more mass on larger
    prizes -> higher RTP). A single loss outcome (0x) absorbs the remaining mass.
    (`decay` is unused; the tilt is solved, not fixed.)

    Feasible RTP range is (hit_rate*min_prize, hit_rate*max_prize)/cost.
    Returns (weight rows, achieved-math analysis)."""
    prizes = sorted({float(p) for p in paytable if p > 0})
    if not prizes:
        raise ValueError("paytable must contain at least one positive multiplier.")
    if not (0 < target_hit_rate < 1):
        raise ValueError("target_hit_rate must be in (0,1).")

    lo_rtp = target_hit_rate * prizes[0] / cost
    hi_rtp = target_hit_rate * prizes[-1] / cost
    if not (lo_rtp < target_rtp < hi_rtp):
        raise ValueError(
            f"Infeasible: at hit-rate {target_hit_rate:.4f} this paytable can only "
            f"produce RTP in ({lo_rtp*100:.3f}%, {hi_rtp*100:.3f}%). "
            f"Adjust hit-rate, RTP, or prize spread.")

    n = len(prizes)

    def tier_probs(base: float) -> List[float]:
        shape = [base ** i for i in range(n)]
        s = sum(shape)
        return [target_hit_rate * sh / s for sh in shape]

    def rtp_for(base: float) -> float:
        return sum(p * pr for p, pr in zip(prizes, tier_probs(base))) / cost

    # rtp_for is monotonically increasing in base -> bisection.
    blo, bhi = 1e-9, 1e9
    for _ in range(200):
        bmid = math.sqrt(blo * bhi)
        if rtp_for(bmid) < target_rtp:
            blo = bmid
        else:
            bhi = bmid
    tier_prob = tier_probs(math.sqrt(blo * bhi))

    # quantise probabilities to integer weights.
    rows: List[WeightRow] = []
    loss_prob = 1.0 - sum(tier_prob)
    rows.append(WeightRow(id=1, weight=max(int(round(loss_prob * granularity)), 1),
                          payout_raw=0, payout=0.0))
    for i, (prize, pr) in enumerate(zip(prizes, tier_prob), start=2):
        w = max(int(round(pr * granularity)), 1)
        raw = int(round(prize * scale))
        rows.append(WeightRow(id=i, weight=w, payout_raw=raw, payout=raw / scale))

    achieved = analyze_weights(rows, cost)
    achieved["target_rtp"] = target_rtp
    achieved["target_hit_rate"] = target_hit_rate
    return rows, achieved


# --------------------------------------------------------------------------- #
# Export helpers                                                               #
# --------------------------------------------------------------------------- #
def write_lookup(rows: Sequence[WeightRow], path: str, header: bool = False) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if header:
            w.writerow(["id", "weight", "payoutMultiplier"])
        for r in rows:
            w.writerow([r.id, r.weight, r.payout_raw])


def write_books_stub(rows: Sequence[WeightRow], path: str,
                     scale: int = BOOK_SCALE) -> None:
    """Emit a minimal books_<mode>.jsonl — one record per distinct outcome, with a
    single payout event. Enough for RGS replay / round-trip validation."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            rec = {
                "id": r.id,
                "payoutMultiplier": r.payout_raw,
                "events": [{"index": 0, "type": "finalWin",
                            "amount": r.payout_raw}],
            }
            fh.write(json.dumps(rec) + "\n")


def write_math_spec(modes_analysis: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(modes_analysis, fh, indent=2)
