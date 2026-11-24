from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol
from uuid import uuid4

from datenwissenschaften.settings import EventSettings, RetroSpeedlabConfig

ATTEMPTS = "runs.attempts"
STEPS = "runs.steps"
RECORDS = "runs.records"
TURN_MINUTES = "runs.turn_minutes"
POLICY_ROUNDS = "policy.rounds"
LAB_EVENTS = "lab.events"


class Events(Protocol):
    def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None: ...


class Unpublished:
    def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        return None


def load_events(settings: EventSettings) -> Events:
    if settings.sink is None:
        return Unpublished()
    module, name = settings.sink.split(":")
    sink: Events = getattr(import_module(module), name)(settings.options)
    return sink


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def run_attributes(settings: RetroSpeedlabConfig) -> dict[str, str]:
    game = settings.speedrun.game
    return {
        "run": uuid4().hex,
        "game": game.id,
        "game_version": game.version,
        "release": settings.ui.release,
        "started_at": now(),
    }
