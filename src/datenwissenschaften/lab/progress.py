from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import typer
from loguru import logger

from datenwissenschaften.lab.tracing import (
    TRACE_SESSION,
    TRACER_NAME,
    exporting_provider,
    score_attributes,
    score_session,
    session_of,
)
from datenwissenschaften.logger import setup_logging
from datenwissenschaften.settings import RetroSpeedlabConfig, load_config

TIMEOUT_SECONDS = 60.0
PROGRESSED = "progressed"
PROGRESS_NAME = "lab progress"


def progress_scores(progress: dict[str, Any]) -> dict[str, float]:
    scores = {PROGRESSED: float(progress[PROGRESSED]), "run_seconds_change": float(progress["seconds"])}
    if progress["hours"] is not None:
        scores["hours_to_progress"] = float(progress["hours"])
    return scores


def measured_runs(settings: RetroSpeedlabConfig) -> list[dict[str, Any]]:
    with httpx.Client(base_url=settings.upload.url, timeout=TIMEOUT_SECONDS) as client:
        response = client.get("/lab-runs")
        response.raise_for_status()
    game = settings.speedrun.game.id
    return [run for run in response.json() if run["game"] == game and run["progress"] is not None]


def score_progress(endpoint: str | None, runs: list[dict[str, Any]]) -> None:
    if endpoint is None:
        logger.warning(f"The progress of {len(runs)} lab runs is not scored: agent.traces is null")
        return
    provider = exporting_provider(endpoint)
    tracer = provider.get_tracer(TRACER_NAME)
    now = datetime.now(UTC)
    for run in runs:
        attributes = {TRACE_SESSION: session_of(run["stamp"]), **score_attributes(progress_scores(run["progress"]))}
        score_session(tracer, PROGRESS_NAME, attributes, now, now)
    provider.shutdown()
    logger.info(f"The progress of {len(runs)} lab runs is scored in the lab traces")


def score_previous_run(settings: RetroSpeedlabConfig, stamp: str) -> None:
    if not settings.ui.enabled:
        return
    try:
        earlier = [run for run in measured_runs(settings) if run["stamp"] < stamp]
    except httpx.HTTPError as error:
        logger.warning(f"The progress before lab run {stamp} was not read: {error}")
        return
    score_progress(settings.lab.traces, earlier[:1])


app = typer.Typer()


@app.command()
def score_all_progress(config: Path) -> None:
    settings = load_config(config)
    setup_logging(settings.log_level)
    score_progress(settings.lab.traces, measured_runs(settings))


if __name__ == "__main__":
    app()
