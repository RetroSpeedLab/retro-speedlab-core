from dataclasses import replace
from pathlib import Path
from typing import Any

from fakes import RecordedEvents, write_config

from datenwissenschaften.events import ATTEMPTS, RECORDS, STEPS, TURN_MINUTES
from datenwissenschaften.settings import SearchTurn, load_config
from datenwissenschaften.speedrun.turn_tracking import TurnMinute, TurnTracking, tracked_events


def attempt(role: str, worker_seconds: float, crossings: int) -> dict[str, Any]:
    return {"role": role, "worker_seconds": worker_seconds, "crossings": crossings}


def step(kind: str, state: str | None, saved: int | None, role: str | None) -> dict[str, Any]:
    return {"kind": kind, "state": state, "saved": saved, "role": role}


def test_a_minute_sums_the_search_events_per_worker_kind() -> None:
    minute = TurnMinute()
    minute.observe(ATTEMPTS, attempt("scout", 30.0, 2))
    minute.observe(ATTEMPTS, attempt("polisher", 10.0, 0))
    minute.observe(STEPS, step("improved", "Level1", 12, "polisher"))
    minute.observe(STEPS, step("stuck", "Boss", None, None))
    minute.observe(STEPS, step("stuck", "Boss", None, None))
    minute.observe(RECORDS, {"seconds": 270.5})

    assert minute.metrics(20.0) == {
        "frames_saved": 12,
        "new_records": 1,
        "crossings": 2,
        "attempts_per_second": 0.1,
        "stuck_states": 1,
    }
    assert minute.kinds() == {
        "scout": {"frames_saved_per_worker_second": 0.0, "worker_share": 0.75},
        "polisher": {"frames_saved_per_worker_second": 1.2, "worker_share": 0.25},
    }


def test_each_flush_publishes_one_minute_and_closing_publishes_the_final_one() -> None:
    events = RecordedEvents()
    tracking = TurnTracking(events, {"run": "r1", "game": "walk", "turn": 7}, 300.0)
    tracking.publish(ATTEMPTS, "walk", attempt("scout", 5.0, 1))
    tracking.flush(False)
    tracking.publish(RECORDS, "walk", {"seconds": 290.0})
    tracking.close()

    assert [topic for topic, _, _ in events.published] == [ATTEMPTS, TURN_MINUTES, RECORDS, TURN_MINUTES]
    minutes = [payload for topic, _, payload in events.published if topic == TURN_MINUTES]
    assert [(minute["step"], minute["best_seconds"], minute["final"]) for minute in minutes] == [
        (0, 300.0, False),
        (1, 290.0, True),
    ]
    assert minutes[0]["kinds"]["scout"]["worker_share"] == 1.0
    assert [minute["new_records"] for minute in minutes] == [0, 1]
    assert minutes[0]["run"] == "r1" and minutes[0]["turn"] == 7


def test_a_search_turn_publishes_the_turn_s_params(tmp_path: Path) -> None:
    settings = load_config(write_config(tmp_path))
    events = RecordedEvents()
    assert tracked_events(settings, events, 60.0) is events
    turned = replace(settings, speedrun=replace(settings.speedrun, turn=SearchTurn(4801, "holdout")))

    tracking = tracked_events(turned, events, 60.0)
    assert isinstance(tracking, TurnTracking)
    tracking.close()
    _, key, minute = events.published[-1]
    assert key == "walk"
    assert (minute["game"], minute["game_version"], minute["release"], minute["turn"], minute["arm"]) == (
        "walk",
        "1.0.0",
        "local",
        4801,
        "holdout",
    )
    assert minute["workers"]["scout"] == settings.speedrun.scouts
