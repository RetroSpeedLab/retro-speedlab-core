import atexit
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import aim

from datenwissenschaften.events import ATTEMPTS, RECORDS, STEPS, Events
from datenwissenschaften.experiments import start_search_turn
from datenwissenschaften.settings import RetroSpeedlabConfig
from datenwissenschaften.speedrun.movie import frames
from datenwissenschaften.speedrun.paths import tas_dir
from datenwissenschaften.speedrun.record import RECORD_FILE, stored_record

TRACK_SECONDS = 60.0
STUCK = "stuck"
KIND = "kind"


class TurnMinute:
    def __init__(self) -> None:
        self.attempts = 0
        self.crossings = 0
        self.frames_saved = 0
        self.new_records = 0
        self.stuck: set[str] = set()
        self.saved: defaultdict[str, int] = defaultdict(int)
        self.worker_seconds: defaultdict[str, float] = defaultdict(float)

    def observe(self, topic: str, payload: dict[str, Any]) -> None:
        if topic == ATTEMPTS:
            self.attempted(payload)
        elif topic == STEPS:
            self.stepped(payload)
        elif topic == RECORDS:
            self.new_records += 1

    def attempted(self, payload: dict[str, Any]) -> None:
        self.attempts += 1
        self.crossings += payload["crossings"]
        self.worker_seconds[payload["role"]] += payload["worker_seconds"]

    def stepped(self, payload: dict[str, Any]) -> None:
        if payload["kind"] == STUCK and payload["state"] is not None:
            self.stuck.add(payload["state"])
        if payload["saved"] is None:
            return
        self.frames_saved += payload["saved"]
        if payload["role"] is not None:
            self.saved[payload["role"]] += payload["saved"]

    def metrics(self, seconds: float) -> dict[str, float]:
        return {
            "frames_saved": self.frames_saved,
            "new_records": self.new_records,
            "crossings": self.crossings,
            "attempts_per_second": self.attempts / seconds,
            "stuck_states": len(self.stuck),
        }

    def kinds(self) -> dict[str, dict[str, float]]:
        total = sum(self.worker_seconds.values())
        return {
            kind: {"frames_saved_per_worker_second": self.saved[kind] / seconds, "worker_share": seconds / total}
            for kind, seconds in self.worker_seconds.items()
            if seconds > 0
        }


class TurnTracking:
    def __init__(self, events: Events, run: aim.Run, best: float | None) -> None:
        self.events = events
        self.run = run
        self.best = best
        self.lock = threading.Lock()
        self.minute = TurnMinute()
        self.started = time.monotonic()
        self.step = 0
        self.stopped = threading.Event()

    def publish(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.minute.observe(topic, payload)
            if topic == RECORDS:
                self.best = payload["seconds"]
        self.events.publish(topic, key, payload)

    def flush(self) -> None:
        now = time.monotonic()
        with self.lock:
            minute, self.minute = self.minute, TurnMinute()
            seconds, self.started = now - self.started, now
            step, self.step = self.step, self.step + 1
            best = self.best
        metrics = minute.metrics(seconds) | ({} if best is None else {"best_seconds": best})
        for name, value in metrics.items():
            self.run.track(value, name=name, step=step)  # pyright: ignore[reportUnknownMemberType]
        for kind, values in minute.kinds().items():
            for name, value in values.items():
                self.run.track(value, name=name, step=step, context={KIND: kind})  # pyright: ignore[reportUnknownMemberType]

    def track_every(self) -> None:
        while not self.stopped.wait(TRACK_SECONDS):
            self.flush()

    def close(self) -> None:
        atexit.unregister(self.close)
        self.stopped.set()
        self.flush()
        self.run.close()


def recorded_seconds(path: Path, frame_rate: float) -> float | None:
    stored = stored_record(path)
    return None if stored is None or not stored.segments else frames(stored.segments) / frame_rate


def tracked_events(settings: RetroSpeedlabConfig, events: Events, frame_rate: float) -> Events:
    turn = settings.experiments.turn
    if turn is None:
        return events
    best = recorded_seconds(tas_dir(settings) / RECORD_FILE, frame_rate)
    tracking = TurnTracking(events, start_search_turn(settings, turn), best)
    threading.Thread(target=tracking.track_every, name="turn-tracking", daemon=True).start()
    atexit.register(tracking.close)
    return tracking
