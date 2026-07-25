"""Game-specific action helpers (the 'verbs' run_spin calls)."""

from game_calculations import GameCalculations
from src.calculations.scatter import Scatter


class GameExecutables(GameCalculations):
    """Grouped, reusable actions for the minimal scatter game."""

    def get_scatterpays_update_wins(self):
        """Evaluate pay-anywhere wins on the current board and update the wallet."""
        # Scatter.get_scatterpay_wins counts every symbol on the board, looks each
        # count up in config.paytable, and returns the win data (also marks winning
        # symbols with .explode for the frontend). During free spins the global
        # multiplier (1x, 2x, 3x ...) scales every win.
        self.win_data = Scatter.get_scatterpay_wins(
            self.config, self.board, global_multiplier=self.global_multiplier
        )
        Scatter.record_scatter_wins(self)          # note wins for the force files
        self.win_manager.tumble_win = self.win_data["totalWin"]
        self.win_manager.update_spinwin(self.win_data["totalWin"])  # add to the wallet
