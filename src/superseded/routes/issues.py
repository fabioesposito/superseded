from __future__ import annotations

import datetime
import re
from datetime import date
from pathlib import Path

import frontmatter
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.github import fetch_github_issue, format_description
from superseded.models import Issue, Stage
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.deps import Deps, get_deps
from superseded.tickets.reader import list_issues
from superseded.tickets.writer import write_issue
from superseded.validation import InvalidInputError, validate_issue_id

router = APIRouter(prefix="/issues")


async def _get_form_data(request: Request):
    """Get form data, checking request.state first (set by CSRF middleware)."""
    if hasattr(request.state, "form_data"):
        return request.state.form_data
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


@router.get("/new", response_class=HTMLResponse)
async def new_issue_form(request: Request, deps: Deps = Depends(get_deps)):
    csrf_token = _csrf_token_for_request(request)
    response = get_templates().TemplateResponse(
        request, "issue_new.html", {"csrf_token": csrf_token}
    )
    if "csrf_token" not in request.cookies:
        response.set_cookie("csrf_token", csrf_token, httponly=False, samesite="lax")
    return response


@router.post("/import", response_class=HTMLResponse)
async def import_github_issue(request: Request, deps: Deps = Depends(get_deps)):
    form = await _get_form_data(request)
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
    form = await _get_form_data(request)
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
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return get_templates().TemplateResponse(
            request,
            "issue_detail.html",
            {
                "issue": None,
                "error": "Invalid issue ID",
                "stage_results": [],
                "stage_order": [s.value for s in Stage],
            },
            status_code=400,
        )
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

    durations: dict[str, str] = {}
    for r in stage_results:
        sa = r.get("started_at")
        fa = r.get("finished_at")
        if sa and fa:
            started = datetime.datetime.fromisoformat(str(sa)) if isinstance(sa, str) else sa
            finished = datetime.datetime.fromisoformat(str(fa)) if isinstance(fa, str) else fa
            dur = (finished - started).total_seconds()
            if dur >= 60:
                durations[r["stage"]] = f"{int(dur // 60)}m {int(dur % 60)}s"
            else:
                durations[r["stage"]] = f"{int(dur)}s"

    questions_content = ""
    questions: list[str] = []
    if issue.pause_reason == "awaiting-input":
        artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue_id)
        questions_file = Path(artifacts_path) / "questions.md"
        if questions_file.exists():
            questions_content = questions_file.read_text(encoding="utf-8")
            for line in questions_content.split("\n"):
                if line.strip().startswith("## Q:"):
                    questions.append(line.strip()[5:].strip())

    response = get_templates().TemplateResponse(
        request,
        "issue_detail.html",
        {
            "issue": issue,
            "stage_results": stage_results,
            "results_by_repo": results_by_repo,
            "harness_iterations": harness_iterations,
            "stage_order": [s.value for s in Stage],
            "passed_stages": [r["stage"] for r in stage_results if r.get("passed")],
            "durations": durations,
            "questions_content": questions_content,
            "questions": questions,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response


@router.get("/{issue_id}/stage/{stage_name}", response_class=HTMLResponse)
async def stage_detail(
    request: Request, issue_id: str, stage_name: str, deps: Deps = Depends(get_deps)
):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return get_templates().TemplateResponse(
            request,
            "stage_detail.html",
            {"issue": None, "stage": None, "error": "Invalid issue ID"},
            status_code=400,
        )

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

    durations: dict[str, str] = {}
    if result:
        sa = result.get("started_at")
        fa = result.get("finished_at")
        if sa and fa:
            started = datetime.datetime.fromisoformat(str(sa)) if isinstance(sa, str) else sa
            finished = datetime.datetime.fromisoformat(str(fa)) if isinstance(fa, str) else fa
            dur = (finished - started).total_seconds()
            if dur >= 60:
                durations[result["stage"]] = f"{int(dur // 60)}m {int(dur % 60)}s"
            else:
                durations[result["stage"]] = f"{int(dur)}s"

    response = get_templates().TemplateResponse(
        request,
        "stage_detail.html",
        {
            "issue": issue,
            "stage": stage,
            "result": result,
            "durations": durations,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response


@router.post("/{issue_id}/answer-questions", response_class=HTMLResponse)
async def answer_questions(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    form = await _get_form_data(request)

    answers_parts = []
    for key, value in form.items():
        if key.startswith("q_"):
            answers_parts.append(f"### {key}\n\n{value}")
    answers_content = "\n\n".join(answers_parts)

    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue_id)
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)
    (Path(artifacts_path) / "answers.md").write_text(answers_content, encoding="utf-8")

    questions_file = Path(artifacts_path) / "questions.md"
    if questions_file.exists():
        questions_file.unlink()

    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return HTMLResponse(content="")
    issue = matching[0]

    from superseded.routes.pipeline import _run_and_advance

    return await _run_and_advance(deps, issue_id, issue.stage, request)


@router.post("/{issue_id}/update-body", response_class=HTMLResponse)
async def update_issue_body(request: Request, issue_id: str, deps: Deps = Depends(get_deps)):
    try:
        issue_id = validate_issue_id(issue_id)
    except InvalidInputError:
        return HTMLResponse(content="")

    form = await _get_form_data(request)
    new_body = str(form.get("body", "")).strip()

    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        return HTMLResponse(content="")

    issue = matching[0]

    with open(issue.filepath) as f:
        post = frontmatter.load(f)
    post.content = new_body
    with open(issue.filepath, "w") as f:
        f.write(frontmatter.dumps(post))

    await deps.db.upsert_issue(
        Issue(
            id=issue.id,
            title=issue.title,
            filepath=issue.filepath,
            body=new_body,
            stage=issue.stage,
            status=issue.status,
        )
    )

    from superseded.routes.pipeline import _run_and_advance

    return await _run_and_advance(deps, issue_id, issue.stage, request)
