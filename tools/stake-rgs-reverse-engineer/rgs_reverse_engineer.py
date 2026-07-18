#!/usr/bin/env python3
"""
rgs_reverse_engineer.py — Reverse-engineer & simulate Stake Engine RGS slot spins.

The Stake RGS does not roll a slot live: for every spin it draws a weighted-random
simulation id from a game's lookUpTable and replays that pre-computed book. So the
lookUpTable (weights + payouts) IS the slot's complete math model. This tool reads
those artifacts, replicates the weighted draw, and reports the full math so you can
replicate any slot's RTP / volatility / hit-rate / win distribution.

Subcommands
  analyze     Exact math from a game's index.json (or explicit lookup CSV).
  simulate    Run N RGS spins on a mode; report a played-out session + a spin log.
  reverse     Rebuild the weight table + math from OBSERVED outcomes (recorded play
              or a books file with no weights).
  replicate   Read a game and emit a portable math-spec JSON + a regenerated
              lookUpTable that reproduces it (round-trippable clone).
  design      Build a lookUpTable that hits a target RTP / hit-rate from a paytable.
  demo        Generate a synthetic Stake game, then analyze + reverse it end-to-end.
  verify      Prove the theoretical math matches simulated spins within tolerance.

Run `python3 rgs_reverse_engineer.py <subcommand> -h` for options.
Pure standard library (optional `zstandard`/`zstd` only for .zst books).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import rgs_core as core


# --------------------------------------------------------------------------- #
# Pretty-printing                                                             #
# --------------------------------------------------------------------------- #
def _fmt_one_in(x: float) -> str:
    if x == math.inf:
        return "never"
    if x < 1:
        return f"{x:.3f}"
    return f"1 in {x:,.1f}"


def print_analysis(name: str, a: dict) -> None:
    print(f"\n=== Mode: {name} ===")
    print(f"  bet cost (x base)   : {a['cost']:.4f}")
    print(f"  distinct outcomes   : {a['n_outcomes']:,}")
    print(f"  total weight        : {a['total_weight']:,}")
    print(f"  RTP                 : {a['rtp_pct']:.4f} %   (edge {100 - a['rtp_pct']:.4f} %)")
    print(f"  mean multiplier     : {a['mean_multiplier']:.6f} x")
    print(f"  std dev (volatility): {a['std_multiplier']:.4f}   CoV {a['coeff_of_variation']:.3f}")
    print(f"  hit rate (>0x)      : {a['hit_rate_any']*100:.4f} %   ({_fmt_one_in(a['hit_one_in_any'])})")
    print(f"  hit rate (>=1x)     : {a['hit_rate_ge1x']*100:.4f} %")
    print(f"  max win             : {a['max_multiplier']:,.2f} x")
    if "rtp_ci95_pct" in a:
        lo, hi = a["rtp_ci95_pct"]
        print(f"  RTP 95% CI (sample) : [{lo:.3f} %, {hi:.3f} %]  (n={a.get('sample_size','?')})")
    print("  win distribution / RTP contribution:")
    print(f"    {'band':<16}{'probability':>14}{'frequency':>16}{'RTP share':>12}")
    for b in a["buckets"]:
        print(f"    {b['band']:<16}{b['prob']*100:>13.4f}%{_fmt_one_in(b['one_in']):>16}"
              f"{b['rtp_contribution']*100:>11.3f}%")


# --------------------------------------------------------------------------- #
# Mode resolution shared by several subcommands                               #
# --------------------------------------------------------------------------- #
def resolve_modes(args) -> list:
    if getattr(args, "index", None):
        modes = core.load_game(args.index, scale=args.scale)
    elif getattr(args, "lookup", None):
        modes = [core.mode_from_files(
            name=getattr(args, "name", None) or "base",
            cost=getattr(args, "cost", 1.0),
            lookup_path=args.lookup,
            books_path=getattr(args, "books", None),
            scale=args.scale)]
    else:
        raise SystemExit("Provide --index <index.json> or --lookup <lookUpTable.csv>.")
    if getattr(args, "mode", None):
        modes = [m for m in modes if m.name == args.mode]
        if not modes:
            raise SystemExit(f"Mode {args.mode!r} not found.")
    return modes


# --------------------------------------------------------------------------- #
# Subcommands                                                                 #
# --------------------------------------------------------------------------- #
def cmd_analyze(args) -> None:
    modes = resolve_modes(args)
    out = {}
    for m in modes:
        a = core.analyze_weights(m.weights, m.cost)
        print_analysis(m.name, a)
        out[m.name] = a
    if len(modes) > 1:
        # blended RTP across selectable modes (equal selection unless costs weight it)
        total = sum(out[m.name]["rtp"] for m in modes) / len(modes)
        print(f"\n  (simple mean RTP across {len(modes)} modes: {total*100:.4f} %)")
    if args.json:
        core.write_math_spec(out, args.json)
        print(f"\nMath spec written -> {args.json}")


def cmd_simulate(args) -> None:
    modes = resolve_modes(args)
    m = modes[0]
    sim = core.RgsSimulator(m.weights, m.cost)
    session = sim.run(args.spins, seed=args.seed)
    s = session.metrics()
    print(f"\n=== Simulated session: mode {m.name!r} | {args.spins:,} spins "
          f"| seed {args.seed} ===")
    print(f"  total bet           : {s['total_bet']:,.2f}")
    print(f"  total won           : {s['total_won']:,.2f}")
    print(f"  net                 : {s['net']:,.2f}")
    print(f"  simulated RTP       : {s['rtp_pct']:.4f} %  (+/- {s['rtp_stderr_pct']:.4f} SE)")
    lo, hi = s["rtp_ci95_pct"]
    print(f"  RTP 95% CI          : [{lo:.3f} %, {hi:.3f} %]")
    print(f"  hit rate (>0x)      : {s['hit_rate_any']*100:.4f} %")
    print(f"  biggest win         : {s['max_multiplier']:,.2f} x")

    theo = core.analyze_weights(m.weights, m.cost)
    print(f"  theoretical RTP     : {theo['rtp_pct']:.4f} %   "
          f"(drift {s['rtp_pct']-theo['rtp_pct']:+.4f} pp)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("spin,simulation_id,multiplier,win,running_rtp\n")
            run_win = 0.0
            for i, (sid, mult) in enumerate(zip(session.ids, session.payouts), 1):
                run_win += mult
                fh.write(f"{i},{sid},{mult:.4f},{mult*1:.4f},"
                         f"{(run_win/(i*m.cost))*100:.4f}\n")
        print(f"  spin log written    -> {args.out}")


_MULT_COL_HINTS = ("multiplier", "payout", "mult", "win_mult", "payoutmultiplier")


def _load_outcomes(path: str, scale: int) -> list:
    """Load a stream of OBSERVED round multipliers (one per played round) from a
    CSV / JSON list, or the distinct outcomes of a books.jsonl."""
    if path.endswith(".jsonl") or path.endswith(".jsonl.zst"):
        print("  WARNING: a books file lists each DISTINCT outcome once, not a "
              "played stream.\n           Its weights live in the lookUpTable — "
              "use `analyze`/`replicate` for exact\n           math. Reversing it "
              "treats every book as equally likely (usually wrong).", file=sys.stderr)
        return [b.payout for b in core.load_books(path, scale).values()]
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data = data.get("multipliers") or data.get("payouts") or []
        return [float(x) for x in data]
    # CSV / whitespace text. Detect a header and prefer a multiplier/payout column.
    with open(path, encoding="utf-8") as fh:
        raw_lines = [ln.strip() for ln in fh if ln.strip()]
    if not raw_lines:
        return []
    header = [c.strip().lower() for c in raw_lines[0].replace(",", " ").split()]
    col = None
    if any(not _is_num(c) for c in header):        # first row is a header
        for hint in _MULT_COL_HINTS:
            if hint in header:
                col = header.index(hint)
                break
        raw_lines = raw_lines[1:]                   # drop the header row

    vals = []
    for line in raw_lines:
        parts = [p.strip() for p in line.replace(",", " ").split()]
        if col is not None and col < len(parts) and _is_num(parts[col]):
            vals.append(float(parts[col]))
            continue
        for p in reversed(parts):                   # fallback: last numeric token
            if _is_num(p):
                vals.append(float(p))
                break
    return vals


def _is_num(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def cmd_reverse(args) -> None:
    mults = _load_outcomes(args.outcomes, args.scale)
    if not mults:
        raise SystemExit(f"No numeric outcomes parsed from {args.outcomes!r}.")
    res = core.reverse_from_outcomes(mults, args.cost, args.scale)
    print(f"\n=== Reverse-engineered from {len(mults):,} observed rounds ===")
    print_analysis(args.name, res["analysis"])
    print("\n  NOTE: RTP/volatility are ESTIMATES; the 95% CI narrows as ~1/sqrt(n).")
    if args.out_lookup:
        core.write_lookup(res["rows"], args.out_lookup)
        print(f"\n  reconstructed lookUpTable -> {args.out_lookup}")
    if args.out_books:
        core.write_books_stub(res["rows"], args.out_books, args.scale)
        print(f"  reconstructed books stub  -> {args.out_books}")


def cmd_replicate(args) -> None:
    modes = core.load_game(args.index, scale=args.scale)
    outdir = args.out_dir
    os.makedirs(outdir, exist_ok=True)
    spec = {}
    index_modes = []
    for m in modes:
        a = core.analyze_weights(m.weights, m.cost)
        print_analysis(m.name, a)
        spec[m.name] = a
        lp = os.path.join(outdir, f"lookUpTable_{m.name}.csv")
        bp = os.path.join(outdir, f"books_{m.name}.jsonl")
        core.write_lookup(m.weights, lp)
        core.write_books_stub(m.weights, bp, args.scale)
        index_modes.append({"name": m.name, "cost": m.cost,
                            "events": os.path.basename(bp),
                            "weights": os.path.basename(lp)})
    core.write_math_spec(spec, os.path.join(outdir, "math_spec.json"))
    with open(os.path.join(outdir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"modes": index_modes}, fh, indent=2)
    print(f"\nReplica package written -> {outdir}/"
          f"\n  index.json, math_spec.json, lookUpTable_*.csv, books_*.jsonl")


def cmd_design(args) -> None:
    paytable = [float(x) for x in args.paytable.split(",")]
    rows, achieved = core.design_weights(
        paytable=paytable, target_rtp=args.rtp, target_hit_rate=args.hit,
        cost=args.cost, decay=args.decay, scale=args.scale,
        granularity=args.granularity)
    print(f"\n=== Designed weight table (target RTP {args.rtp*100:.2f}%, "
          f"hit {args.hit*100:.2f}%) ===")
    print_analysis(args.name, achieved)
    print(f"\n  achieved RTP {achieved['rtp_pct']:.4f}% vs target "
          f"{args.rtp*100:.4f}%  (delta {achieved['rtp_pct']-args.rtp*100:+.4f} pp)")
    if args.out_lookup:
        core.write_lookup(rows, args.out_lookup)
        print(f"  lookUpTable -> {args.out_lookup}")
    if args.out_books:
        core.write_books_stub(rows, args.out_books, args.scale)
        print(f"  books stub  -> {args.out_books}")


def cmd_verify(args) -> None:
    modes = resolve_modes(args)
    m = modes[0]
    theo = core.analyze_weights(m.weights, m.cost)
    sim = core.RgsSimulator(m.weights, m.cost)
    s = sim.run(args.spins, seed=args.seed).metrics()
    drift = s["rtp_pct"] - theo["rtp_pct"]
    tol = args.tol
    ok = abs(drift) <= tol
    print(f"\n=== Verify mode {m.name!r} ({args.spins:,} spins) ===")
    print(f"  theoretical RTP : {theo['rtp_pct']:.4f} %")
    print(f"  simulated  RTP : {s['rtp_pct']:.4f} %  (SE {s['rtp_stderr_pct']:.4f})")
    print(f"  drift          : {drift:+.4f} pp   tolerance +/-{tol} pp")
    print(f"  RESULT         : {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


def cmd_demo(args) -> None:
    import synth_slot
    synth_slot.run_demo(args.out_dir, spins=args.spins, seed=args.seed,
                        scale=args.scale)


# --------------------------------------------------------------------------- #
# Argument parsing                                                            #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rgs_reverse_engineer.py",
        description="Reverse-engineer & simulate Stake Engine RGS slot spins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--scale", type=int, default=core.BOOK_SCALE,
                   help=f"book payout scale (default {core.BOOK_SCALE}: raw 100 = 1x).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_source(sp):
        sp.add_argument("--index", help="path to index.json / config.json")
        sp.add_argument("--lookup", help="path to a lookUpTable_*.csv (single mode)")
        sp.add_argument("--books", help="optional books_*.jsonl(.zst) for that mode")
        sp.add_argument("--cost", type=float, default=1.0, help="bet cost for --lookup mode")
        sp.add_argument("--name", default="base", help="mode name for --lookup mode")
        sp.add_argument("--mode", help="restrict to one mode name (with --index)")

    a = sub.add_parser("analyze", help="exact math from a game")
    add_source(a); a.add_argument("--json", help="write math spec JSON here")
    a.set_defaults(func=cmd_analyze)

    s = sub.add_parser("simulate", help="run N RGS spins")
    add_source(s)
    s.add_argument("--spins", type=int, default=100000)
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--out", help="write per-spin CSV log here")
    s.set_defaults(func=cmd_simulate)

    r = sub.add_parser("reverse", help="rebuild math from observed outcomes")
    r.add_argument("outcomes", help="CSV/JSON of multipliers, or a books.jsonl")
    r.add_argument("--cost", type=float, default=1.0)
    r.add_argument("--name", default="observed")
    r.add_argument("--out-lookup", help="write reconstructed lookUpTable here")
    r.add_argument("--out-books", help="write reconstructed books stub here")
    r.set_defaults(func=cmd_reverse)

    rp = sub.add_parser("replicate", help="clone a game into a portable spec")
    rp.add_argument("--index", required=True)
    rp.add_argument("--out-dir", default="replica")
    rp.set_defaults(func=cmd_replicate)

    d = sub.add_parser("design", help="design a weight table to targets")
    d.add_argument("--paytable", required=True, help="comma list of win multipliers")
    d.add_argument("--rtp", type=float, required=True, help="target RTP (0-1)")
    d.add_argument("--hit", type=float, required=True, help="target hit rate (0-1)")
    d.add_argument("--cost", type=float, default=1.0)
    d.add_argument("--decay", type=float, default=0.35,
                   help="geometric rarity decay across prize tiers (default 0.35)")
    d.add_argument("--granularity", type=int, default=10_000_000,
                   help="integer-weight resolution; raise for exact tail RTP "
                        "(default 10,000,000)")
    d.add_argument("--name", default="designed")
    d.add_argument("--out-lookup"); d.add_argument("--out-books")
    d.set_defaults(func=cmd_design)

    v = sub.add_parser("verify", help="theoretical vs simulated RTP within tolerance")
    add_source(v)
    v.add_argument("--spins", type=int, default=2_000_000)
    v.add_argument("--seed", type=int, default=12345)
    v.add_argument("--tol", type=float, default=0.5, help="tolerance in RTP pp")
    v.set_defaults(func=cmd_verify)

    dm = sub.add_parser("demo", help="synthetic game -> analyze -> reverse round-trip")
    dm.add_argument("--out-dir", default="demo_game")
    dm.add_argument("--spins", type=int, default=500000)
    dm.add_argument("--seed", type=int, default=2026)
    dm.set_defaults(func=cmd_demo)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
