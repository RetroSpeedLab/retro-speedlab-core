from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fakes import RecordedEvents, write_config

from datenwissenschaften.events import ATTEMPTS, RECORDS, STEPS
from datenwissenschaften.settings import ExperimentSettings, SearchTurn, load_config
from datenwissenschaften.speedrun.turn_tracking import TurnMinute, TurnTracking, tracked_events


@dataclass
class TrackedRun:
    tracked: list[tuple[str, float, int, dict[str, str]]] = field(default_factory=list)
    closed: bool = False

    def track(self, value: float, name: str, step: int, context: dict[str, str] | None = None) -> None:
        self.tracked.append((name, value, step, context or {}))

    def close(self) -> None:
        self.closed = True


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


def test_each_flush_tracks_one_minute_step_and_closing_tracks_the_rest() -> None:
    events, run = RecordedEvents(), TrackedRun()
    tracking = TurnTracking(events, run, 300.0)
    tracking.publish(ATTEMPTS, "walk", attempt("scout", 5.0, 1))
    tracking.flush()
    tracking.publish(RECORDS, "walk", {"seconds": 290.0})
    tracking.close()

    assert [topic for topic, _, _ in events.published] == [ATTEMPTS, RECORDS]
    best = [(value, at) for name, value, at, _ in run.tracked if name == "best_seconds"]
    assert best == [(300.0, 0), (290.0, 1)]
    assert ("worker_share", 1.0, 0, {"kind": "scout"}) in run.tracked
    assert [value for name, value, at, _ in run.tracked if name == "new_records"] == [0, 1]
    assert run.closed


def test_a_search_turn_is_one_aim_run_with_the_turn_s_params(tmp_path: Path) -> None:
    settings = load_config(write_config(tmp_path))
    events = RecordedEvents()
    assert tracked_events(settings, events, 60.0) is events
    turn = replace(settings, experiments=ExperimentSettings(str(tmp_path), SearchTurn(4801, "holdout")))

    tracking = tracked_events(turn, events, 60.0)
    assert isinstance(tracking, TurnTracking)
    run = tracking.run
    assert (run["kind"], run["game"], run["release"], run["turn"], run["arm"]) == (
        "search_turn",
        "walk",
        "local",
        4801,
        "holdout",
    )
    assert run["workers"]["scout"] == settings.speedrun.scouts
    tracking.close()
