"""Minimal scatter-pays game WITH a free-spins feature.

Base game: 5x3 pay-anywhere board. Land 3+ scatters ("S") and you win a
number of free spins. During free spins a global multiplier starts at 1x
and grows +1 every spin, so more scatters -> more spins -> bigger multipliers.
"""

import os
from src.config.config import Config
from src.config.distributions import Distribution
from src.config.betmode import BetMode


class GameConfig(Config):
    """All game-specific parameters live here (the 'rulebook')."""

    def __init__(self):
        super().__init__()
        self.game_id = "0_0_minimal"
        self.provider_number = 0
        self.working_name = "Minimal Scatter Game (with Free Spins)"
        self.wincap = 500.0
        self.win_type = "scatter"
        self.rtp = 0.95
        self.construct_paths()

        # --- Board dimensions: 5 reels, 3 rows each ---
        self.num_reels = 5
        self.num_rows = [3] * self.num_reels

        # --- Paytable (balanced): pays by count of a symbol anywhere on the board ---
        r1, r2, r3 = (3, 4), (5, 6), (7, 15)
        pay_group = {
            (r1, "H1"): 2.0, (r2, "H1"): 10.0, (r3, "H1"): 80.0,  # high symbol
            (r1, "H2"): 1.0, (r2, "H2"): 4.0,  (r3, "H2"): 40.0,
            (r1, "L1"): 0.5, (r2, "L1"): 2.0,  (r3, "L1"): 15.0,
            (r1, "L2"): 0.3, (r2, "L2"): 1.5,  (r3, "L2"): 10.0,  # low symbol
            ((16, 16), "X"): 0.0,   # blank filler (never pays; creates losses)
        }
        self.paytable = self.convert_range_table(pay_group)

        # "S" is the scatter: it never pays directly, it only triggers free spins.
        self.include_padding = True
        self.special_symbols = {"wild": [], "scatter": ["S"]}

        # --- Free-spin triggers: <scatters on board> : <free spins awarded> ---
        self.freespin_triggers = {
            self.basegame_type: {3: 8, 4: 12, 5: 15},   # first trigger, from the base game
            self.freegame_type: {3: 5, 4: 8, 5: 10},    # retrigger, during free spins
        }
        # Show "anticipation" (reel slow-down) once we're one scatter away from a trigger.
        self.anticipation_triggers = {
            self.basegame_type: min(self.freespin_triggers[self.basegame_type].keys()) - 1,
            self.freegame_type: min(self.freespin_triggers[self.freegame_type].keys()) - 1,
        }

        # --- Reels: reuse one strip for both base and free games ---
        reels = {"BR0": "BR0.csv"}
        self.reels = {}
        for r, f in reels.items():
            self.reels[r] = self.read_reels_csv(os.path.join(self.reels_path, f))
        self.padding_reels[self.basegame_type] = self.reels["BR0"]
        self.padding_reels[self.freegame_type] = self.reels["BR0"]

        # --- One bet mode, THREE outcome categories the simulator must produce ---
        self.bet_modes = [
            BetMode(
                name="base",
                cost=1.0,
                rtp=self.rtp,
                max_win=self.wincap,
                auto_close_disabled=False,
                is_feature=True,
                is_buybonus=False,
                distributions=[
                    # (1) Pure losing spins.
                    Distribution(
                        criteria="0",
                        quota=0.35,
                        win_criteria=0.0,
                        conditions={
                            "reel_weights": {self.basegame_type: {"BR0": 1}},
                            "force_wincap": False,
                            "force_freegame": False,
                        },
                    ),
                    # (2) Ordinary base-game wins (no free spins; scatters redrawn away).
                    Distribution(
                        criteria="basegame",
                        quota=0.55,
                        conditions={
                            "reel_weights": {self.basegame_type: {"BR0": 1}},
                            "force_wincap": False,
                            "force_freegame": False,
                        },
                    ),
                    # (3) Forced free-game rounds: the board is forced to land scatters.
                    Distribution(
                        criteria="freegame",
                        quota=0.10,
                        conditions={
                            "reel_weights": {
                                self.basegame_type: {"BR0": 1},
                                self.freegame_type: {"BR0": 1},
                            },
                            "scatter_triggers": {3: 8, 4: 3, 5: 1},  # how many scatters to force
                            "force_wincap": False,
                            "force_freegame": True,
                        },
                    ),
                ],
            ),
        ]
