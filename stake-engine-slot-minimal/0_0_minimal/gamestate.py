"""The heart of the game: base spin + free-spins feature."""

from game_override import GameStateOverride
from src.events.events import set_win_event, set_total_event


class GameState(GameStateOverride):
    """Scatter-pays game with a growing-multiplier free-spins feature."""

    def run_spin(self, sim, simulation_seed=None):
        self.reset_seed(sim)
        self.repeat = True
        while self.repeat:
            self.reset_book()
            self.draw_board()

            # --- base-game evaluation ---
            self.get_scatterpays_update_wins()
            self.emit_tumble_win_events()
            self.win_manager.update_gametype_wins(self.gametype)   # file as base-game wins
            if self.win_manager.spin_win > 0:
                set_win_event(self)
            set_total_event(self)

            # --- free-spin trigger: enough scatters AND this distribution wants a feature ---
            if self.check_fs_condition() and self.check_freespin_entry():
                self.run_freespin_from_base()   # awards spins, then calls run_freespin()

            self.evaluate_finalwin()
            self.check_repeat()

        self.imprint_wins()

    def run_freespin(self):
        """One free-spins session. Multiplier starts at 1x and grows +1 each spin."""
        self.reset_fs_spin()                       # gametype -> freegame, fs=0, multiplier=1
        while self.fs < self.tot_fs:
            self.update_freespin()                 # fs += 1, reset this spin's win, emit event
            self.draw_board()

            self.get_scatterpays_update_wins()      # wins scaled by self.global_multiplier
            self.emit_tumble_win_events()
            self.win_manager.update_gametype_wins(self.gametype)   # file as free-game wins
            if self.win_manager.spin_win > 0:
                set_win_event(self)
            set_total_event(self)

            self.update_global_mult()               # multiplier +1 for the NEXT spin (emits event)

            # retrigger: more scatters during a free spin adds more spins
            if self.check_fs_condition():
                self.update_fs_retrigger_amt()

        self.end_freespin()                         # emit total-feature-win event
