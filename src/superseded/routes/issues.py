from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.github import fetch_github_issue, format_description
from superseded.models import Issue, Stage
from superseded.routes import get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import write_issue

router = APIRouter(prefix="/issues")


@router.get("/new", response_class=HTMLResponse)
async def new_issue_form(request: Request, deps: Deps = Depends(get_deps)):
    return get_templates().TemplateResponse(request, "issue_new.html", {})


@router.post("/import", response_class=HTMLResponse)
async def import_github_issue(request: Request, deps: Deps = Depends(get_deps)):
    form = await request.form()
    github_url = str(form.get("github_url", "")).strip()

    try:
        gh_issue = await fetch_github_issue(github_url)
    except (ValueError, RuntimeError) as e:
        return get_templates().TemplateResponse(
            request,
            "issue_new.html",
            {"error": str(e)},
        )

    description = format_description(gh_issue.body, gh_issue.comments)
    labels_str = ", ".join(gh_issue.labels)

    return get_templates().TemplateResponse(
        request,
        "issue_new.html",
        {
            "title": gh_issue.title,
            "body": description,
            "labels": labels_str,
            "assignee": gh_issue.assignee,
            "github_url": gh_issue.url,
        },
    )


@router.post("/new", response_class=RedirectResponse)
async def create_issue(request: Request, deps: Deps = Depends(get_deps)):
    form = await request.form()
    title = str(form.get("title", "")).strip()
    body = str(form.get("body", "")).strip()
    labels_str = str(form.get("labels", "")).strip()
    assignee = str(form.get("assignee", "")).strip()
    github_url = str(form.get("github_url", "")).strip()

    labels = [l.strip() for l in labels_str.split(",") if l.strip()] if labels_str else []

    repos_str = str(form.get("repos", "")).strip()
    repos = [r.strip() for r in repos_str.split(",") if r.strip()] if repos_str else []

    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    Path(issues_dir).mkdir(parents=True, exist_ok=True)

    issue_id = await deps.db.next_issue_id()
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    filepath = str(Path(issues_dir) / f"{issue_id}-{slug}.md")

    labels_yaml = "\n".join(f"  - {l}" for l in labels) if labels else "  []"
    repos_yaml = "\n".join(f"  - {r}" for r in repos) if repos else "  []"
    github_url_line = f'github_url: "{github_url}"' if github_url else ""
    content = f"""---
id: {issue_id}
title: {title}
status: new
stage: spec
created: "{date.today().isoformat()}"
assignee: {assignee}
labels:
{labels_yaml}
repos:
{repos_yaml}
{github_url_line}
---

{body}
"""
    write_issue(filepath, content)

    issue = Issue(
        id=issue_id,
        title=title,
        filepath=filepath,
        assignee=assignee,
        labels=labels,
        repos=repos,
    )
    await deps.db.upsert_issue(issue)

    return RedirectResponse(url=f"/issues/{issue_id}", status_code=303)


@router.get("/{issue_id}", response_class=HTMLResponse)
async def issue_detail(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return get_templates().TemplateResponse(
            request,
            "issue_detail.html",
            {
                "issue": None,
                "error": "Issue not found",
                "stage_results": [],
                "stage_order": [s.value for s in Stage],
            },
            status_code=404,
        )

    issue = matching[0]
    stage_results = await deps.db.get_stage_results(issue_id)
    harness_iterations = await deps.db.get_harness_iterations(issue_id)

    results_by_repo: dict[str, list] = {}
    for r in stage_results:
        repo = r.get("repo", "primary")
        results_by_repo.setdefault(repo, []).append(r)

    return get_templates().TemplateResponse(
        request,
        "issue_detail.html",
        {
            "issue": issue,
            "stage_results": stage_results,
            "results_by_repo": results_by_repo,
            "harness_iterations": harness_iterations,
            "stage_order": [s.value for s in Stage],
            "passed_stages": [r["stage"] for r in stage_results if r.get("passed")],
        },
    )


@router.get("/{issue_id}/stage/{stage_name}", response_class=HTMLResponse)
async def stage_detail(
    request: Request, issue_id: str, stage_name: str, deps: Deps = Depends(get_deps)
):
    try:
        stage = Stage(stage_name)
    except ValueError:
        return get_templates().TemplateResponse(
            request,
            "stage_detail.html",
            {
                "issue": None,
                "stage": None,
                "error": f"Invalid stage: {stage_name}",
            },
            status_code=400,
        )

    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return get_templates().TemplateResponse(
            request,
            "stage_detail.html",
            {
                "issue": None,
                "stage": stage,
                "error": "Issue not found",
            },
            status_code=404,
        )

    issue = matching[0]
    result = None
    results = await deps.db.get_stage_results(issue_id)
    for r in results:
        if r["stage"] == stage_name:
            result = r
            break

    return get_templates().TemplateResponse(
        request,
        "stage_detail.html",
        {
            "issue": issue,
            "stage": stage,
            "result": result,
        },
    )
