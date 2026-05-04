from __future__ import annotations

import datetime
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from superseded.github import fetch_github_issue, format_description
from superseded.models import Issue, Stage, StageResult
from superseded.routes import _csrf_token_for_request, get_templates
from superseded.routes.deps import Deps, get_deps, get_issue
from superseded.routes.service import format_durations, run_and_advance
from superseded.tickets.reader import read_issue
from superseded.tickets.writer import delete_issue_file, update_issue_body, write_issue

router = APIRouter(prefix="/issues")


def _render_error(request: Request, message: str, status_code: int = 400) -> HTMLResponse:
    return get_templates().TemplateResponse(
        request,
        "issue_detail.html",
        {
            "issue": None,
            "error": message,
            "stage_results": [],
            "stage_order": [s.value for s in Stage],
        },
        status_code=status_code,
    )


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
async def issue_detail(
    request: Request, issue: Issue = Depends(get_issue), deps: Deps = Depends(get_deps)
):
    stage_results = await deps.db.get_stage_results(issue.id)
    harness_iterations = await deps.db.get_harness_iterations(issue.id)

    results_by_repo: dict[str, list] = {}
    for r in stage_results:
        repo = r.get("repo", "primary")
        results_by_repo.setdefault(repo, []).append(r)

    durations = format_durations(stage_results)

    questions_content = ""
    questions: list[str] = []
    if issue.pause_reason == "awaiting-input":
        artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
        questions_file = Path(artifacts_path) / "questions.md"
        if questions_file.exists():
            questions_content = questions_file.read_text(encoding="utf-8")
            for line in questions_content.split("\n"):
                if line.strip().startswith("## Q:"):
                    questions.append(line.strip()[5:].strip())

    approval_content = ""
    if issue.pause_reason == "approval-required":
        artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
        repos = issue.repos if issue.repos else [None]
        for repo_name in repos:
            effective_repo = repo_name or "primary"
            approval_file = Path(artifacts_path) / effective_repo / "approval.md"
            if approval_file.exists():
                approval_content = approval_file.read_text(encoding="utf-8")
                break

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
            "approval_content": approval_content,
        },
    )
    if "csrf_token" not in request.cookies:
        token = _csrf_token_for_request(request)
        response.set_cookie("csrf_token", token, httponly=False, samesite="lax")
    return response


@router.get("/{issue_id}/stage/{stage_name}", response_class=HTMLResponse)
async def stage_detail(
    request: Request,
    stage_name: str,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
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

    result = None
    results = await deps.db.get_stage_results(issue.id)
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
async def answer_questions(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)

    answers_parts = []
    for key, value in form.items():
        if key.startswith("q_"):
            answers_parts.append(f"### {key}\n\n{value}")
    answers_content = "\n\n".join(answers_parts)

    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
    Path(artifacts_path).mkdir(parents=True, exist_ok=True)
    (Path(artifacts_path) / "answers.md").write_text(answers_content, encoding="utf-8")

    questions_file = Path(artifacts_path) / "questions.md"
    if questions_file.exists():
        questions_file.unlink()

    for repo_name in issue.repos if issue.repos else [None]:
        effective_repo = repo_name or "primary"
        approval_file = Path(artifacts_path) / effective_repo / "approval.md"
        if approval_file.exists():
            approval_file.unlink()

    await deps.db.update_pause_reason(issue.id, "")

    return await run_and_advance(deps, issue.id, request, background_tasks)


@router.post("/{issue_id}/delete", response_class=RedirectResponse)
async def delete_issue_handler(
    request: Request,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    delete_issue_file(issue.filepath)
    await deps.db.delete_issue(issue.id)

    return RedirectResponse(url="/", status_code=303)


@router.post("/{issue_id}/update-body", response_class=HTMLResponse)
async def update_issue_body_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    body = str(form.get("body", "")).strip()

    update_issue_body(issue.filepath, body)

    updated_issue = read_issue(issue.filepath)
    await deps.db.upsert_issue(updated_issue)
    await deps.db.update_pause_reason(issue.id, "")

    return await run_and_advance(deps, issue.id, request, background_tasks)


@router.post("/{issue_id}/approve", response_class=HTMLResponse)
async def approve_issue(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
    for repo_name in issue.repos if issue.repos else [None]:
        effective_repo = repo_name or "primary"
        approval_file = Path(artifacts_path) / effective_repo / "approval.md"
        if approval_file.exists():
            approval_file.unlink()

    await deps.db.update_pause_reason(issue.id, "")
    return await run_and_advance(deps, issue.id, request, background_tasks)


@router.post("/{issue_id}/reject", response_class=HTMLResponse)
async def reject_issue(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    feedback = str(form.get("feedback", "")).strip()

    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
    for repo_name in issue.repos if issue.repos else [None]:
        effective_repo = repo_name or "primary"
        result = StageResult(
            stage=issue.stage,
            passed=False,
            output="",
            error=f"User rejected with feedback: {feedback}",
        )
        await deps.db.save_stage_result(issue.id, result, repo=effective_repo)

        approval_file = Path(artifacts_path) / effective_repo / "approval.md"
        if approval_file.exists():
            approval_file.unlink()

    await deps.db.update_pause_reason(issue.id, "")
    return await run_and_advance(deps, issue.id, request, background_tasks)


@router.post("/{issue_id}/approve-file", response_class=HTMLResponse)
async def approve_file(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    filename = str(form.get("filename", "")).strip()
    if not filename:
        return _render_error(request, "Filename is required")

    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)

    approvals_file = Path(artifacts_path) / "file-approvals.txt"
    with open(approvals_file, "a") as f:
        f.write(f"APPROVED: {filename}\n")

    changed_files = _get_changed_files(artifacts_path)
    approved = _get_approved_files(artifacts_path)
    if changed_files and all(f in approved for f in changed_files):
        for repo_name in issue.repos if issue.repos else [None]:
            effective_repo = repo_name or "primary"
            approval_file = Path(artifacts_path) / effective_repo / "approval.md"
            if approval_file.exists():
                approval_file.unlink()
        await deps.db.update_pause_reason(issue.id, "")
        return await run_and_advance(deps, issue.id, request, background_tasks)

    return get_templates().TemplateResponse(
        request,
        "_file_approval.html",
        {"issue": issue, "changed_files": changed_files, "approved_files": approved},
    )


@router.post("/{issue_id}/reject-file", response_class=HTMLResponse)
async def reject_file(
    request: Request,
    background_tasks: BackgroundTasks,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    form = await _get_form_data(request)
    filename = str(form.get("filename", "")).strip()
    feedback = str(form.get("feedback", "")).strip()
    if not filename:
        return _render_error(request, "Filename is required")

    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)

    result = StageResult(
        stage=issue.stage,
        passed=False,
        output="",
        error=f"File rejected: {filename} — {feedback}",
    )
    await deps.db.save_stage_result(issue.id, result)

    approvals_file = Path(artifacts_path) / "file-approvals.txt"
    if approvals_file.exists():
        approvals_file.unlink()

    for repo_name in issue.repos if issue.repos else [None]:
        effective_repo = repo_name or "primary"
        approval_file = Path(artifacts_path) / effective_repo / "approval.md"
        if approval_file.exists():
            approval_file.unlink()

    await deps.db.update_pause_reason(issue.id, "")
    return await run_and_advance(deps, issue.id, request, background_tasks)


@router.get("/{issue_id}/files", response_class=HTMLResponse)
async def get_file_approval(
    request: Request,
    issue: Issue = Depends(get_issue),
    deps: Deps = Depends(get_deps),
):
    artifacts_path = str(Path(deps.config.repo_path) / deps.config.artifacts_dir / issue.id)
    changed_files = _get_changed_files(artifacts_path)
    approved = _get_approved_files(artifacts_path)
    return get_templates().TemplateResponse(
        request,
        "_file_approval.html",
        {"issue": issue, "changed_files": changed_files, "approved_files": approved},
    )


def _get_changed_files(artifacts_path: str) -> list[str]:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1"],
            capture_output=True,
            text=True,
            cwd=artifacts_path,
            timeout=5,
        )
        if result.returncode == 0:
            return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except Exception:
        pass
    return []


def _get_approved_files(artifacts_path: str) -> set[str]:
    approvals_file = Path(artifacts_path) / "file-approvals.txt"
    if not approvals_file.exists():
        return set()
    approved = set()
    for line in approvals_file.read_text().split("\n"):
        if line.startswith("APPROVED: "):
            approved.add(line[10:].strip())
    return approved


async def _get_form_data(request: Request) -> dict:
    if hasattr(request.state, "form_data"):
        return request.state.form_data
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}
