"""Optimization targets for the free-spins game.

Now there are THREE criteria (matching the three distributions). Each one's
target RTP is a CONTRIBUTION; they must sum to the bet mode RTP (0.95).
    losses (0)  +  base-game wins  +  free-game wins  =  0.95
"""

from optimization_program.optimization_config import (
    ConstructScaling,
    ConstructParameters,
    ConstructFenceBias,
    ConstructConditions,
    verify_optimization_input,
)


class OptimizationSetup:
    """Optimization parameters for each bet mode."""

    def __init__(self, game_config):
        self.game_config = game_config
        self.game_config.opt_params = {
            "base": {
                "conditions": {
                    # losing spins: contribute 0 to RTP
                    "0": ConstructConditions(rtp=0, av_win=0, search_conditions=0).return_dict(),
                    # base under-delivers on this small pool, so keep its target tiny...
                    "basegame": ConstructConditions(rtp=0.05, hr=3.5).return_dict(),
                    # ...and let the FEATURE carry almost all the RTP (it hits its target).
                    "freegame": ConstructConditions(
                        rtp=0.90, hr=100, search_conditions={"symbol": "scatter"}
                    ).return_dict(),
                },
                "scaling": ConstructScaling(
                    [
                        {"criteria": "basegame", "scale_factor": 1.0, "win_range": (1, 2), "probability": 1.0},
                    ]
                ).return_dict(),
                "parameters": ConstructParameters(
                    num_show=5000,
                    num_per_fence=10000,
                    min_m2m=1,
                    max_m2m=30,
                    pmb_rtp=1.0,
                    sim_trials=5000,
                    test_spins=[50, 100, 200],
                    test_weights=[0.3, 0.4, 0.3],
                    score_type="rtp",
                    max_trial_dist=15,
                ).return_dict(),
                "distribution_bias": ConstructFenceBias(
                    applied_criteria=["basegame"],
                    bias_ranges=[(1.0, 2.0)],
                    bias_weights=[0.3],
                ).return_dict(),
            },
        }

        verify_optimization_input(self.game_config, self.game_config.opt_params)
