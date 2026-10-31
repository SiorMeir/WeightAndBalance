"""Stateless FastAPI adapter for the robust fuel-cap solver.

Educational only. This service is not approved for aircraft loading, dispatch,
or flight operations.
"""

from __future__ import annotations

import itertools
import logging
import os
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

import wb_core as wb
from web_examples import example_requests

ALL_CENTROGRAMS_LIMIT = 10_000

logger = logging.getLogger("wb_api")
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_start_var: ContextVar[float | None] = ContextVar("request_start", default=None)


class RequestIdFilter(logging.Filter):
    """Make the request id available to both API and solver log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging() -> None:
    """Install the request-aware log format without adding duplicate filters."""
    level = logging._nameToLevel.get(os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(request_id)s %(name)s: %(message)s",
        )
    else:
        root.setLevel(level)

    for target in (root, logger, logging.getLogger("wb_core")):
        if not any(isinstance(item, RequestIdFilter) for item in target.filters):
            target.addFilter(RequestIdFilter())
    for handler in root.handlers:
        if not any(isinstance(item, RequestIdFilter) for item in handler.filters):
            handler.addFilter(RequestIdFilter())
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(request_id)s %(name)s: %(message)s"))


configure_logging()


class EnvelopePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cg: float
    weight: float


class Tail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    weight: float = Field(gt=0)
    cg: float


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    weight: float = Field(ge=0)
    cg: float
    weight_delta: float = Field(default=0, ge=0)
    cg_delta: float = Field(default=0, ge=0)
    optional: bool = False

    @model_validator(mode="after")
    def weight_range_is_non_negative(self) -> Item:
        if self.weight_delta > self.weight:
            raise ValueError("weight_delta must not exceed weight")
        return self


class FuelPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weight: float = Field(ge=0)
    moment: float


class CalcOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all_centrograms: bool = False
    max_steps: int = Field(default=5000, ge=1, le=100_000)


class CalcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: list[EnvelopePoint] = Field(min_length=3, max_length=200)
    tails: list[Tail] = Field(min_length=1, max_length=50)
    items: list[Item] = Field(default_factory=list, max_length=40)
    fuel_flow: list[FuelPoint] = Field(min_length=2, max_length=500)
    options: CalcOptions = Field(default_factory=CalcOptions)

    @field_validator("tails")
    @classmethod
    def unique_tail_ids(cls, tails: list[Tail]) -> list[Tail]:
        if len({tail.id for tail in tails}) != len(tails):
            raise ValueError("tail ids must be unique")
        return tails

    @field_validator("items")
    @classmethod
    def unique_item_names(cls, items: list[Item]) -> list[Item]:
        if len({item.name for item in items}) != len(items):
            raise ValueError("item names must be unique")
        return items

    @field_validator("fuel_flow")
    @classmethod
    def cumulative_fuel_flow(cls, points: list[FuelPoint]) -> list[FuelPoint]:
        first = points[0]
        if first.weight != 0 or first.moment != 0:
            raise ValueError("first fuel point must be (0, 0)")
        if any(next_point.weight <= point.weight for point, next_point in zip(points, points[1:])):
            raise ValueError("fuel weights must strictly increase")
        return points


class Point(BaseModel):
    cg: float
    weight: float


class ItemCentrogram(BaseModel):
    name: str
    present: bool
    weight: float
    cg: Optional[float]


class CentrogramPoint(BaseModel):
    fuel: float
    weight: float
    cg: float


class EnvelopeEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: Point = Field(alias="from")
    to: Point


class Centrogram(BaseModel):
    tail: str
    items: list[ItemCentrogram]
    zero_fuel: Point
    cap: float
    binding: bool
    limited_by_edge: Optional[EnvelopeEdge]
    cap_point: Optional[Point]
    points: list[CentrogramPoint]


class CalcStats(BaseModel):
    loadings: int
    exact_evaluations: int
    certificates: int
    pruned: int
    steps: int


class Timing(BaseModel):
    solve_ms: Optional[float]
    centrograms_ms: Optional[float]
    total_ms: float


class ErrorDetail(BaseModel):
    field: str
    problem: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail]


class CalcResponse(BaseModel):
    request_id: str
    status: Literal["ok", "incomplete", "error"]
    fuel_cap: Optional[float]
    fuel_cap_upper_bound: Optional[float]
    full_fuel: Optional[float]
    limiting: Optional[Centrogram]
    critical_centrograms: Optional[list[Centrogram]]
    all_centrograms: Optional[list[Centrogram]]
    stats: Optional[CalcStats]
    timing: Timing
    error: Optional[ErrorInfo]


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, details: list[dict[str, str]] | None = None, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.http_status = http_status


def request_id() -> str:
    return request_id_var.get()


def elapsed_ms() -> float:
    started = request_start_var.get()
    return round((time.perf_counter() - started) * 1000, 1) if started is not None else 0.0


def error_response(code: str, message: str, http_status: int, details: list[dict[str, str]] | None = None) -> JSONResponse:
    payload = CalcResponse(
        request_id=request_id(),
        status="error",
        fuel_cap=None,
        fuel_cap_upper_bound=None,
        full_fuel=None,
        limiting=None,
        critical_centrograms=None,
        all_centrograms=None,
        stats=None,
        timing=Timing(solve_ms=None, centrograms_ms=None, total_ms=elapsed_ms()),
        error=ErrorInfo(code=code, message=message, details=details or []),
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump(mode="json", by_alias=True))


def validation_details(errors: list[dict]) -> list[dict[str, str]]:
    details = []
    for error in errors:
        location = [str(part) for part in error["loc"] if part != "body"]
        details.append({"field": ".".join(location) or "$", "problem": error["msg"]})
    return details


def build_problem(payload: CalcRequest) -> wb.Problem:
    """Translate the public cumulative data contract into the solver's inputs."""
    items: dict[str, list[tuple[float, float]]] = {}
    for item in payload.items:
        weights = sorted({item.weight - item.weight_delta, item.weight + item.weight_delta})
        cgs = sorted({item.cg - item.cg_delta, item.cg + item.cg_delta})
        options = [(weight, cg) for weight in weights for cg in cgs]
        if item.optional:
            options.append((0.0, 0.0))
        items[item.name] = options
        logger.debug("item %r options=%s", item.name, options)

    tanks = []
    previous = payload.fuel_flow[0]
    for index, point in enumerate(payload.fuel_flow[1:], start=1):
        fuel_weight = point.weight - previous.weight
        fuel_moment = point.moment - previous.moment
        tanks.append((f"fuel {index}", fuel_weight, fuel_moment / fuel_weight))
        previous = point
    logger.debug("fuel increments=%s", tanks)

    try:
        problem = wb.Problem(
            envelope=[(point.cg, point.weight) for point in payload.envelope],
            basics=[(tail.id, tail.weight, tail.cg) for tail in payload.tails],
            fixed=[],
            items=items,
            tanks=tanks,
        )
    except ValueError as exc:
        raise ApiError("invalid_input", str(exc)) from None

    logger.debug("normalised envelope=%s", problem.envelope)
    return problem


def point_from_state(state: wb.State) -> Point:
    return Point(weight=state[0], cg=state[1] / state[0])


def centrogram_for(problem: wb.Problem, cfg: tuple, result: wb.Result, tail_ids: list[str]) -> Centrogram:
    """Build one client-facing centrogram from an exact solver configuration."""
    # ponytail: Result retains configurations, rather than their individual caps;
    # recomputing is cheap for the bounded response sizes.
    cap, _, edge = problem.cap(problem.base_of(cfg))
    base = problem.base_of(cfg)
    items = []
    for name, states, index in zip(problem.slot_names[1:], problem.slot_states[1:], cfg[1:]):
        weight, moment = states[index]
        items.append(ItemCentrogram(name=name, present=weight != 0, weight=weight, cg=moment / weight if weight else None))

    if edge is None:
        limited_by_edge = None
    else:
        limited_by_edge = EnvelopeEdge(
            from_=Point(cg=problem.envelope[edge][0], weight=problem.envelope[edge][1]),
            to=Point(
                cg=problem.envelope[(edge + 1) % len(problem.envelope)][0],
                weight=problem.envelope[(edge + 1) % len(problem.envelope)][1],
            ),
        )

    cap_point = point_from_state(problem.centrogram(base, cap)[-1]) if cap < problem.full - wb.EPS else None
    points = [
        CentrogramPoint(fuel=state[0] - base[0], weight=state[0], cg=state[1] / state[0])
        for state in problem.centrogram(base, problem.full)
    ]
    return Centrogram(
        tail=tail_ids[cfg[0]],
        items=items,
        zero_fuel=point_from_state(base),
        cap=cap,
        binding=cfg == result.config,
        limited_by_edge=limited_by_edge,
        cap_point=cap_point,
        points=points,
    )


app = FastAPI(
    title="Robust fuel-cap API",
    description="Educational weight-and-balance calculation service. Not dispatch software.",
    version="0.1.0",
)


@app.get("/ui/examples")
def ui_examples() -> list[dict]:
    return [
        {**example, "request": CalcRequest.model_validate(example["request"]).model_dump()}
        for example in example_requests()
    ]


@app.get("/ui/plotly.min.js", include_in_schema=False)
def plotly_asset() -> FileResponse:
    import plotly

    return FileResponse(
        Path(plotly.__file__).parent / "package_data" / "plotly.min.js",
        media_type="text/javascript",
    )


app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="ui")


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_token = request_id_var.set(uuid.uuid4().hex)
    start_token = request_start_var.set(time.perf_counter())
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id()
        return response
    finally:
        request_start_var.reset(start_token)
        request_id_var.reset(request_token)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = validation_details(exc.errors())
    logger.warning("invalid_request: Request body failed validation.")
    return error_response("invalid_request", "Request body failed validation.", 422, details)


@app.exception_handler(ApiError)
async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
    logger.warning("%s: %s", exc.code, exc.message)
    return error_response(exc.code, exc.message, exc.http_status, exc.details)


@app.exception_handler(Exception)
async def handle_internal_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("internal_error")
    return error_response("internal_error", "An unexpected internal error occurred.", 500)


@app.post("/standard-calc", response_model=CalcResponse)
def standard_calc(payload: CalcRequest) -> CalcResponse:
    problem = build_problem(payload)
    tail_ids = [tail.id for tail in payload.tails]
    if payload.options.all_centrograms and problem.total > ALL_CENTROGRAMS_LIMIT:
        raise ApiError(
            "too_many_centrograms",
            "Too many centrograms requested.",
            details=[{
                "field": "options.all_centrograms",
                "problem": f"{problem.total} loadings > {ALL_CENTROGRAMS_LIMIT}",
            }],
        )

    solve_started = time.perf_counter()
    try:
        result = wb.solve(problem, budget=payload.options.max_steps)
    except ValueError as exc:
        raise ApiError("infeasible_at_zero_fuel", str(exc)) from None
    solve_ms = round((time.perf_counter() - solve_started) * 1000, 1)

    centrograms_started = time.perf_counter()
    evaluated = sorted(
        (centrogram_for(problem, cfg, result, tail_ids) for cfg in result.evaluated),
        key=lambda centrogram: (centrogram.cap, not centrogram.binding),
    )
    limiting = next((centrogram for centrogram in evaluated if centrogram.binding), None)
    all_centrograms = (
        [centrogram_for(problem, cfg, result, tail_ids) for cfg in itertools.product(*(range(len(slot)) for slot in problem.slot_states))]
        if payload.options.all_centrograms
        else None
    )
    centrograms_ms = round((time.perf_counter() - centrograms_started) * 1000, 1)

    response = CalcResponse(
        request_id=request_id(),
        status="ok" if result.complete else "incomplete",
        fuel_cap=result.cap if result.complete else None,
        fuel_cap_upper_bound=result.cap,
        full_fuel=problem.full,
        limiting=limiting,
        critical_centrograms=evaluated,
        all_centrograms=all_centrograms,
        stats=CalcStats(
            loadings=problem.total,
            exact_evaluations=result.exact_evals,
            certificates=result.certificates,
            pruned=result.pruned,
            steps=len(result.trace),
        ),
        timing=Timing(solve_ms=solve_ms, centrograms_ms=centrograms_ms, total_ms=elapsed_ms()),
        error=None,
    )
    logger.info(
        "tails=%d items=%d loadings=%d status=%s fuel_cap=%s steps=%d solve_ms=%.1f total_ms=%.1f",
        len(payload.tails), len(payload.items), problem.total, response.status,
        response.fuel_cap if response.fuel_cap is not None else response.fuel_cap_upper_bound,
        len(result.trace), solve_ms, response.timing.total_ms,
    )
    return response
