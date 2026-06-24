from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


async def checkout_repo(
    token: str,
    owner: str,
    repo: str,
    ref: str,
    base_ref: str,
    tmp_dir: str,
) -> Path:
    target = Path(tmp_dir)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
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
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {proc.returncode}): " + stderr.decode(errors="replace").strip()
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
            f"git checkout failed (exit {checkout_proc.returncode}): "
            + stderr.decode(errors="replace").strip()
        )

    return target
