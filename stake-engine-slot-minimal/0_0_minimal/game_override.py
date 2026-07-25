"""Overrides/extensions of the universal state.py behaviour."""

from game_executables import GameExecutables


class GameStateOverride(GameExecutables):
    """Hook points for per-game state resets and special symbols."""

    def reset_book(self):
        # Clear the framework's per-round state, then any of our own (none here).
        super().reset_book()

    def reset_fs_spin(self):
        # Start every free-spins session with a 1x multiplier.
        super().reset_fs_spin()
        self.global_multiplier = 1

    def assign_special_sym_function(self):
        # No wild/multiplier symbols on the board; the multiplier is global.
        pass
