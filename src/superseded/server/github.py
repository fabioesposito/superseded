from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

import httpx
import jwt


class GitHubApp:
    def __init__(self, app_id: int, private_key_path: Path, webhook_secret: str) -> None:
        self.app_id = app_id
        self._private_key = private_key_path.read_text()
        self._webhook_secret = webhook_secret.encode()

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        if not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(self._webhook_secret, payload, hashlib.sha256).hexdigest()
        actual = signature[len("sha256=") :]
        return hmac.compare_digest(expected, actual)

    def _api_headers(self, token: str, accept: str = "application/vnd.github+json") -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _sign_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 600,
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        jwt_token = self._sign_jwt()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers=self._api_headers(jwt_token),
            )
            response.raise_for_status()
            return response.json()["token"]

    async def fetch_pr_diff(self, token: str, owner: str, repo: str, pr_number: int) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self._api_headers(token, accept="application/vnd.github.v3.diff"),
            )
            response.raise_for_status()
            return response.text

    async def fetch_pr_description(
        self, token: str, owner: str, repo: str, pr_number: int
    ) -> str | None:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                headers=self._api_headers(token),
            )
            response.raise_for_status()
            data = response.json()
            body = data.get("body", "")
            return body if body else None

    async def post_review(
        self,
        token: str,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
        event: str,
    ) -> list[int]:
        payload = {
            "event": event,
            "body": body,
            "comments": comments,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                headers=self._api_headers(token),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return [c["id"] for c in data.get("comments", []) if "id" in c]

    async def create_check_run(
        self,
        token: str,
        owner: str,
        repo: str,
        name: str,
        head_sha: str,
        status: str,
        conclusion: str | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> int:
        payload: dict = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion is not None:
            payload["conclusion"] = conclusion
        if title is not None:
            payload["output"] = {"title": title, "summary": summary or ""}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/check-runs",
                headers=self._api_headers(token),
                json=payload,
            )
            response.raise_for_status()
            return response.json()["id"]

    async def update_check_run(
        self,
        token: str,
        owner: str,
        repo: str,
        check_run_id: int,
        status: str,
        conclusion: str | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> int:
        payload: dict = {"status": status}
        if conclusion is not None:
            payload["conclusion"] = conclusion
        if title is not None:
            payload["output"] = {"title": title, "summary": summary or ""}
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"https://api.github.com/repos/{owner}/{repo}/check-runs/{check_run_id}",
                headers=self._api_headers(token),
                json=payload,
            )
            response.raise_for_status()
            return check_run_id
