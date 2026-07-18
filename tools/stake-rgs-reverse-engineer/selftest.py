"""Self-test: asserts the core math invariants. Run: python3 selftest.py"""
import math
import rgs_core as core
import synth_slot


def approx(a, b, tol):
    assert abs(a - b) <= tol, f"{a} vs {b} (tol {tol})"


def test_analyze_matches_manual():
    rows = [
        core.WeightRow(id=1, weight=90, payout_raw=0, payout=0.0),
        core.WeightRow(id=2, weight=9, payout_raw=200, payout=2.0),   # 2x
        core.WeightRow(id=3, weight=1, payout_raw=1000, payout=10.0), # 10x
    ]
    a = core.analyze_weights(rows, cost=1.0)
    # RTP = (90*0 + 9*2 + 1*10)/100 = 0.28
    approx(a["rtp"], 0.28, 1e-12)
    approx(a["hit_rate_any"], 0.10, 1e-12)
    assert a["max_multiplier"] == 10.0
    # sum of bucket RTP contributions == total RTP
    approx(sum(b["rtp_contribution"] for b in a["buckets"]), a["rtp"], 1e-12)
    print("ok  analyze matches manual computation + buckets sum to RTP")


def test_sim_converges_to_theory():
    rows = synth_slot.enumerate_outcomes()
    theo = core.analyze_weights(rows, 1.0)
    sim = core.RgsSimulator(rows, 1.0).run(2_000_000, seed=123).metrics()
    # within ~3 standard errors
    approx(sim["rtp"], theo["rtp"], 4 * sim["rtp_stderr_pct"] / 100 + 1e-3)
    print(f"ok  simulated RTP {sim['rtp']*100:.3f}% converges to theory "
          f"{theo['rtp']*100:.3f}%")


def test_reverse_recovers():
    rows = synth_slot.enumerate_outcomes()
    session = core.RgsSimulator(rows, 1.0).run(3_000_000, seed=7)
    theo = core.analyze_weights(rows, 1.0)
    rev = core.reverse_from_outcomes(session.payouts, 1.0)["analysis"]
    lo, hi = rev["rtp_ci95_pct"]
    assert lo <= theo["rtp"] * 100 <= hi, (lo, theo["rtp"] * 100, hi)
    assert rev["max_multiplier"] == theo["max_multiplier"]
    print(f"ok  reverse recovers RTP; true {theo['rtp']*100:.3f}% inside "
          f"95% CI [{lo:.3f}, {hi:.3f}]")


def test_design_hits_both_targets():
    rows, a = core.design_weights([0.5, 1, 2, 5, 10, 50, 200, 1000],
                                  target_rtp=0.96, target_hit_rate=0.30)
    approx(a["rtp"], 0.96, 1e-3)
    approx(a["hit_rate_any"], 0.30, 1e-3)
    print(f"ok  design hits both targets: RTP {a['rtp']*100:.3f}%, "
          f"hit {a['hit_rate_any']*100:.3f}%")


def test_design_infeasible_raises():
    try:
        core.design_weights([2, 5], target_rtp=0.96, target_hit_rate=0.10)
    except ValueError:
        print("ok  design rejects infeasible targets")
        return
    raise AssertionError("expected ValueError for infeasible design")


def test_pf_is_deterministic():
    rows = synth_slot.enumerate_outcomes()
    sim = core.RgsSimulator(rows, 1.0)
    r1 = sim.spin_pf("srv", "cli", 5)
    r2 = sim.spin_pf("srv", "cli", 5)
    assert r1.id == r2.id, "provably-fair draw must be reproducible"
    print("ok  provably-fair draw is deterministic")


if __name__ == "__main__":
    test_analyze_matches_manual()
    test_sim_converges_to_theory()
    test_reverse_recovers()
    test_design_hits_both_targets()
    test_design_infeasible_raises()
    test_pf_is_deterministic()
    print("\nALL SELF-TESTS PASSED")
