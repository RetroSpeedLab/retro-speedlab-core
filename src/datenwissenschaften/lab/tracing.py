from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import msgspec
from loguru import logger
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, StatusCode, Tracer

from datenwissenschaften.lab.transcript import AGENT, GENERATION, TOOL, USAGE_KEYS, Round, Step

TRACER_NAME = "retro-speedlab-lab"
TRACE_NAME = "lab round"
SCORES_NAME = "lab scores"
SESSION_PREFIX = "lab-run "
REJECTED = "rejected"
DEPLOYED = "deployed"
OUTCOMES = frozenset({REJECTED, "notes", DEPLOYED})
MAX_TEXT = 4000
NANOSECONDS = 1_000_000_000
SECONDS_PER_MINUTE = 60
EXPORT_TIMEOUT_SECONDS = 60
TRACES_PATH = "/v1/traces"
KIND = "lab.kind"
INPUT = "lab.input"
OUTPUT = "lab.output"
MODEL = "gen_ai.response.model"
USAGE_PREFIX = "lab.usage."
SCORE_PREFIX = "score."
TRACE_SESSION = "session.id"


@dataclass(slots=True, frozen=True)
class LabRun:
    game: str
    stamp: str
    base: str
    outcome: str
    commits: int

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown lab run outcome {self.outcome!r}, expected one of {sorted(OUTCOMES)}")


def nanoseconds(moment: datetime) -> int:
    return int(moment.timestamp() * NANOSECONDS)


def clipped(value: Any) -> str:
    return (value if isinstance(value, str) else msgspec.json.encode(value).decode())[:MAX_TEXT]


def step_attributes(step: Step) -> dict[str, Any]:
    attributes: dict[str, Any] = {KIND: step.kind, OUTPUT: clipped(step.output)}
    if step.input is not None:
        attributes[INPUT] = clipped(step.input)
    if step.kind == GENERATION:
        attributes[MODEL] = step.name
        attributes |= {f"{USAGE_PREFIX}{name}": count for name, count in step.usage.items()}
    return attributes


class RoundExporter:
    def __init__(self, tracer: Tracer, steps: list[Step]) -> None:
        self.tracer = tracer
        keys = {step.key for step in steps}
        self.children: dict[str | None, list[Step]] = {}
        for step in steps:
            self.children.setdefault(step.owner if step.owner in keys else None, []).append(step)

    def below(self, key: str | None) -> list[Step]:
        return self.children[key] if key in self.children else []

    def emit(self, step: Step, parent: Span) -> datetime:
        context = trace.set_span_in_context(parent)
        span = self.tracer.start_span(
            step.name, context, start_time=nanoseconds(step.start), attributes=step_attributes(step)
        )
        if step.failed:
            span.set_status(StatusCode.ERROR)
        end = max([step.end, *(self.emit(child, span) for child in self.below(step.key))])
        span.end(end_time=nanoseconds(end))
        return end


def session_of(stamp: str) -> str:
    return f"{SESSION_PREFIX}{stamp}"


def run_attributes(run: LabRun) -> dict[str, Any]:
    return {TRACE_SESSION: session_of(run.stamp), "lab.game": run.game, "lab.stamp": run.stamp, "lab.base": run.base}


def export_round(tracer: Tracer, run: LabRun, number: int, lab_round: Round) -> str:
    root = tracer.start_span(
        f"{TRACE_NAME} {number}",
        start_time=nanoseconds(lab_round.start),
        attributes={
            **run_attributes(run),
            "lab.outcome": run.outcome,
            "lab.round": number,
            "lab.cost_usd": lab_round.cost,
            **round_attributes(lab_round.steps),
            KIND: AGENT,
            OUTPUT: clipped(lab_round.result),
        },
    )
    exporter = RoundExporter(tracer, lab_round.steps)
    for step in exporter.below(None):
        exporter.emit(step, root)
    root.end(end_time=nanoseconds(lab_round.end))
    return format(root.get_span_context().trace_id, "032x")


def export_run(tracer: Tracer, run: LabRun, rounds: list[Round]) -> list[str]:
    return [export_round(tracer, run, number, lab_round) for number, lab_round in enumerate(rounds, 1)]


def usage_totals(steps: list[Step]) -> Counter[str]:
    return sum((Counter(step.usage) for step in steps if step.kind == GENERATION), Counter[str]())


def step_counts(steps: list[Step]) -> dict[str, float]:
    return {
        "turns": float(sum(step.kind == GENERATION for step in steps)),
        "subagents": float(sum(step.kind == AGENT for step in steps)),
        "tool_calls": float(sum(step.kind == TOOL for step in steps)),
        "tool_errors": float(sum(step.failed for step in steps)),
        "steps": float(len(steps)),
        "tokens": float(usage_totals(steps).total()),
    }


def round_attributes(steps: list[Step]) -> dict[str, Any]:
    models = Counter(step.name for step in steps if step.kind == GENERATION).most_common(1)
    totals = usage_totals(steps)
    usage = {f"{USAGE_PREFIX}{name}": totals[name] for name in USAGE_KEYS.values()}
    counts = {f"lab.{name}": int(value) for name, value in step_counts(steps).items()}
    return {**counts, **usage, **({MODEL: models[0][0]} if models else {})}


def run_scores(run: LabRun, rounds: list[Round]) -> dict[str, float]:
    steps = [step for lab_round in rounds for step in lab_round.steps]
    return {
        "accepted": float(run.outcome != REJECTED),
        DEPLOYED: float(run.outcome == DEPLOYED),
        "cost_usd": sum(lab_round.cost for lab_round in rounds),
        "commits": float(run.commits),
        "minutes": (rounds[-1].end - rounds[0].start).total_seconds() / SECONDS_PER_MINUTE,
        **step_counts(steps),
    }


def score_attributes(scores: dict[str, float]) -> dict[str, float]:
    return {f"{SCORE_PREFIX}{name}": value for name, value in scores.items()}


def score_session(tracer: Tracer, name: str, attributes: dict[str, Any], start: datetime, end: datetime) -> None:
    span = tracer.start_span(name, start_time=nanoseconds(start), attributes=attributes)
    span.end(end_time=nanoseconds(end))


def trace_run(tracer: Tracer, run: LabRun, rounds: list[Round]) -> list[str]:
    trace_ids = export_run(tracer, run, rounds)
    attributes = {**run_attributes(run), **score_attributes(run_scores(run, rounds))}
    score_session(tracer, SCORES_NAME, attributes, rounds[0].start, rounds[-1].end)
    return trace_ids


def exporting_provider(endpoint: str) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: TRACER_NAME}))
    exporter = OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}{TRACES_PATH}", timeout=EXPORT_TIMEOUT_SECONDS)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def send_trace(endpoint: str | None, run: LabRun, rounds: list[Round]) -> None:
    if endpoint is None:
        logger.warning(f"The lab run {run.stamp} is not traced: agent.traces is null")
        return
    provider = exporting_provider(endpoint)
    trace_ids = trace_run(provider.get_tracer(TRACER_NAME), run, rounds)
    provider.shutdown()
    logger.info(f"The lab run {run.stamp} is traced as session {session_of(run.stamp)!r} with {len(trace_ids)} rounds")
