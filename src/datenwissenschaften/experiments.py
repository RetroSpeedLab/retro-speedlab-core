from typing import Any

import aim

from datenwissenschaften.settings import RetroSpeedlabConfig, SearchTurn
from datenwissenschaften.speedrun.live import BACKTRACKER, BEAM_SCOUT, POLISHER, SCOUT, VISION_SCOUT

PPO_ROUND = "ppo_round"
SEARCH_TURN = "search_turn"
SYSTEM_SECONDS = 30


def started_run(settings: RetroSpeedlabConfig, kind: str, name: str) -> aim.Run:
    release = settings.ui.release
    game = settings.speedrun.game
    run = aim.Run(
        repo=settings.experiments.repo,
        experiment=release,
        capture_terminal_logs=False,
        system_tracking_interval=SYSTEM_SECONDS,
    )
    run.name = name
    run["kind"] = kind
    run["game"] = game.id
    run["game_version"] = game.version
    run["release"] = release
    for tag in (game.id, game.category):
        run.add_tag(tag)  # pyright: ignore[reportUnknownMemberType]
    return run


def start_experiment(settings: RetroSpeedlabConfig, hyperparameters: dict[str, Any]) -> aim.Run:
    speedrun = settings.speedrun
    run = started_run(settings, PPO_ROUND, f"ppo {speedrun.game.id}")
    run["hparams"] = {
        "category": speedrun.game.category,
        "policy_emulators": speedrun.policy_emulators,
        **hyperparameters,
    }
    return run


def start_search_turn(settings: RetroSpeedlabConfig, turn: SearchTurn) -> aim.Run:
    speedrun = settings.speedrun
    run = started_run(settings, SEARCH_TURN, f"search {speedrun.game.id} turn {turn.index}")
    run["turn"] = turn.index
    run["arm"] = turn.arm
    run["workers"] = {
        SCOUT: speedrun.scouts,
        BACKTRACKER: speedrun.backtrackers,
        BEAM_SCOUT: speedrun.beam_scouts,
        VISION_SCOUT: speedrun.vision_scouts,
        POLISHER: speedrun.polishers,
    }
    return run
