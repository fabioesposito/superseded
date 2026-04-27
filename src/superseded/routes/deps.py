from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request

from superseded.models import Issue
from superseded.routes.service import Deps, PipelineState, get_deps
from superseded.tickets.reader import list_issues
from superseded.validation import InvalidInputError, validate_issue_id


async def get_validated_issue_id(issue_id: str) -> str:
    try:
        return validate_issue_id(issue_id)
    except InvalidInputError:
        raise HTTPException(status_code=400, detail="Invalid issue ID") from None


async def get_issue(request: Request, issue_id: str = Depends(get_validated_issue_id)) -> Issue:
    deps: Deps = await get_deps(request)
    issues_dir = str(Path(deps.config.repo_path) / deps.config.issues_dir)
    matching = [i for i in list_issues(issues_dir) if i.id == issue_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Issue not found")
    return matching[0]


__all__ = ["Deps", "PipelineState", "get_deps", "get_issue", "get_validated_issue_id"]
