"""Game-specific calculations layer.

For this minimal game we add nothing of our own — the base Executables
already provide everything. This file only exists to keep the standard
inheritance chain: Executables -> GameCalculations -> GameExecutables
-> GameStateOverride -> GameState.
"""

from src.executables.executables import Executables


class GameCalculations(Executables):
    """No custom math needed for the minimal game."""

    pass
