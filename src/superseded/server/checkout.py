from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
from pathlib import Path


def _build_clone_env(token: str) -> dict:
    """Build an environment for git clone that passes the token via env vars.

    Using ``GIT_CONFIG_*`` env vars keeps the token out of the process
    argument list (visible to all users via ``/proc/<pid>/cmdline`` / ``ps``).
    Env vars are only visible to the same user (or root) via
    ``/proc/<pid>/environ``.
    """
    env = os.environ.copy()
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"Authorization: basic {encoded}"
    return env


def _redact_token(text: str, token: str) -> str:
    """Strip any occurrence of *token* (and URL-embedded variants) from text."""
    if not token:
        return text
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    patterns = [
        token,
        f"x-access-token:{token}",
        encoded,
        re.escape(token),
    ]
    result = text
    for pat in patterns:
        result = result.replace(pat, "***")
    return result


async def checkout_repo(
    token: str,
    owner: str,
    repo: str,
    ref: str,
    tmp_dir: str,
) -> Path:
    target = Path(tmp_dir)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    url = f"https://github.com/{owner}/{repo}.git"
    clone_env = _build_clone_env(token)
    clone_cmd = [
        "git",
        "clone",
        "--depth=2",
        url,
        str(target),
    ]
    proc = await asyncio.create_subprocess_exec(
        *clone_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=clone_env,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            "git clone failed (exit "
            f"{proc.returncode}): " + _redact_token(stderr.decode(errors="replace").strip(), token)
        )

    checkout_proc = await asyncio.create_subprocess_exec(
        "git",
        "checkout",
        ref,
        cwd=str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await checkout_proc.communicate()
    if checkout_proc.returncode != 0:
        raise RuntimeError(
            "git checkout failed (exit "
            f"{checkout_proc.returncode}): "
            + _redact_token(stderr.decode(errors="replace").strip(), token)
        )

    return target
