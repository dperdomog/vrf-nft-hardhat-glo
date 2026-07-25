"""Driver for the minimal game."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "games", "0_0_minimal"))

from gamestate import GameState
from game_config import GameConfig
from src.state.run_sims import create_books
from src.write_data.write_configs import generate_configs

DO_OPT = os.environ.get("DO_OPT", "0") == "1"
NSIMS = int(os.environ.get("NSIMS", "10000"))

config = GameConfig()
gamestate = GameState(config)

if DO_OPT:
    from game_optimization import OptimizationSetup
    from optimization_program.run_script import OptimizationExecution
    OptimizationSetup(config)

t0 = time.time()
create_books(gamestate, config, {"base": NSIMS}, 5000, 4, True, False)
generate_configs(gamestate)
print(f"[sim] {time.time()-t0:.0f}s for {NSIMS} sims")

if DO_OPT:
    t1 = time.time()
    OptimizationExecution().run_all_modes(config, ["base"], 8)
    generate_configs(gamestate)
    print(f"[opt] {time.time()-t1:.0f}s")
    print("DONE_OPT")
