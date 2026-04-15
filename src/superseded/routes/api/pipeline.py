from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends

from superseded.models import PipelineMetrics
from superseded.routes.deps import Deps, get_deps

api_router = APIRouter(prefix="/api/pipeline")


@api_router.get("/metrics")
async def get_metrics(deps: Deps = Depends(get_deps)):
    return await _compute_metrics(deps)


async def _compute_metrics(deps: Deps) -> dict:
    issues = await deps.db.list_issues()
    total = len(issues)
    by_status: dict[str, int] = {}
    for issue in issues:
        s = issue["status"]
        by_status[s] = by_status.get(s, 0) + 1

    all_results = []
    for issue in issues:
        results = await deps.db.get_stage_results(issue["id"])
        all_results.extend(results)

    stage_attempts: dict[str, list[bool]] = {}
    for r in all_results:
        stage_attempts.setdefault(r["stage"], []).append(r["passed"])

    success_rates = {
        stage: sum(1 for p in passes if p) / len(passes) for stage, passes in stage_attempts.items()
    }

    all_iterations = []
    for issue in issues:
        iters = await deps.db.get_harness_iterations(issue["id"])
        all_iterations.extend(iters)

    total_retries = len(all_iterations)
    retries_by_stage: dict[str, int] = {}
    for it in all_iterations:
        retries_by_stage[it["stage"]] = retries_by_stage.get(it["stage"], 0) + 1

    stage_durations: dict[str, list[float]] = {}
    for r in all_results:
        started = r.get("started_at")
        finished = r.get("finished_at")
        if started and finished:
            try:
                dt_start = datetime.datetime.fromisoformat(started)
                dt_end = datetime.datetime.fromisoformat(finished)
                dur = (dt_end - dt_start).total_seconds() * 1000
                if dur > 0:
                    stage_durations.setdefault(r["stage"], []).append(dur)
            except (ValueError, TypeError):
                pass

    avg_stage_duration_ms = {
        stage: sum(durs) / len(durs) for stage, durs in stage_durations.items()
    }

    return PipelineMetrics(
        total_issues=total,
        issues_by_status=by_status,
        stage_success_rates=success_rates,
        avg_stage_duration_ms=avg_stage_duration_ms,
        total_retries=total_retries,
        retries_by_stage=retries_by_stage,
        recent_events=[],
    ).model_dump()
