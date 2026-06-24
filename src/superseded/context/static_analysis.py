from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import ClassVar, Protocol

logger = logging.getLogger(__name__)

STATIC_BUDGET = 4000

LANG_PYTHON = "python"
LANG_JS = "js"
LANG_TS = "ts"
LANG_GO = "go"
LANG_ANY = "*"

EXT_MAP: dict[str, str] = {
    ".py": LANG_PYTHON,
    ".js": LANG_JS,
    ".jsx": LANG_JS,
    ".mjs": LANG_JS,
    ".cjs": LANG_JS,
    ".ts": LANG_TS,
    ".tsx": LANG_TS,
    ".go": LANG_GO,
}


class Tool(Protocol):
    name: str
    languages: list[str]

    def detect(self, root: Path) -> bool: ...
    def build_command(self, changed_files: list[str], root: Path) -> list[str]: ...
    def parse_output(
        self, stdout: str, stderr: str, root: Path, changed_files: list[str]
    ) -> str: ...


def _detect_pyproject_dep(root: Path, dep: str) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    text = pyproject.read_text()
    return f"[tool.{dep}]" in text or f'"{dep}"' in text or f"'{dep}'" in text


class RuffTool:
    name: ClassVar[str] = "ruff"
    languages: ClassVar[list[str]] = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "ruff")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["ruff", "check", "--output-format=concise", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stdout.strip() if stdout.strip() else ""


class MypyTool:
    name: ClassVar[str] = "mypy"
    languages: ClassVar[list[str]] = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "mypy")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["mypy", "--no-error-summary", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stdout.strip() if stdout.strip() else ""


class BanditTool:
    name: ClassVar[str] = "bandit"
    languages: ClassVar[list[str]] = [LANG_PYTHON]

    def detect(self, root: Path) -> bool:
        return _detect_pyproject_dep(root, "bandit")

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["bandit", "-q", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stdout.strip() if stdout.strip() else ""


class EslintTool:
    name: ClassVar[str] = "eslint"
    languages: ClassVar[list[str]] = [LANG_JS, LANG_TS]

    def detect(self, root: Path) -> bool:
        if list(root.glob(".eslintrc*")):
            return True
        if list(root.glob("eslint.config.*")):
            return True
        pkg = root / "package.json"
        return pkg.exists() and "eslintConfig" in pkg.read_text()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["eslint", "--format=compact", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stdout.strip() if stdout.strip() else ""


class TscTool:
    name: ClassVar[str] = "tsc"
    languages: ClassVar[list[str]] = [LANG_TS]

    def detect(self, root: Path) -> bool:
        return (root / "tsconfig.json").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["tsc", "--noEmit"]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stderr.strip() if stderr.strip() else ""


class GofmtTool:
    name: ClassVar[str] = "gofmt"
    languages: ClassVar[list[str]] = [LANG_GO]

    def detect(self, root: Path) -> bool:
        return (root / "go.mod").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        go_files = [f for f in changed_files if f.endswith(".go")]
        return ["gofmt", "-l", *go_files] if go_files else ["gofmt", "-l", "."]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        if not stdout.strip():
            return ""
        files = [f"  {f}" for f in stdout.strip().splitlines()]
        return "Files needing formatting:\n" + "\n".join(files)


class GoVetTool:
    name: ClassVar[str] = "go vet"
    languages: ClassVar[list[str]] = [LANG_GO]

    def detect(self, root: Path) -> bool:
        return (root / "go.mod").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["go", "vet", "."]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        combined = (stdout + "\n" + stderr).strip()
        return combined if combined else ""


class StaticcheckTool:
    name: ClassVar[str] = "staticcheck"
    languages: ClassVar[list[str]] = [LANG_GO]

    def detect(self, root: Path) -> bool:
        if not (root / "go.mod").exists():
            return False
        from shutil import which

        return which("staticcheck") is not None

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return ["staticcheck", *changed_files]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        return stdout.strip() if stdout.strip() else ""


class GitleaksTool:
    name: ClassVar[str] = "gitleaks"
    languages: ClassVar[list[str]] = [LANG_ANY]

    def detect(self, root: Path) -> bool:
        return (root / ".git").exists()

    def build_command(self, changed_files: list[str], root: Path) -> list[str]:
        return [
            "gitleaks",
            "dir",
            "scan",
            "--source",
            str(root),
            "--no-banner",
            "--report-format",
            "json",
        ]

    def parse_output(self, stdout: str, stderr: str, root: Path, changed_files: list[str]) -> str:
        import json

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError, ValueError:
            return ""
        changed = set(changed_files)
        findings = []
        for item in data:
            file = item.get("File", "")
            if file not in changed:
                continue
            desc = item.get("Description", "")
            line = item.get("StartLine", "")
            findings.append(f"  {file}:{line} — {desc}")
        return "Secrets detected:\n" + "\n".join(findings) if findings else ""


TOOLS: list[Tool] = [
    RuffTool(),
    MypyTool(),
    BanditTool(),
    EslintTool(),
    TscTool(),
    GofmtTool(),
    GoVetTool(),
    StaticcheckTool(),
    GitleaksTool(),
]


def _languages_in_files(changed_files: list[str]) -> set[str]:
    langs: set[str] = set()
    for f in changed_files:
        ext = Path(f).suffix
        lang = EXT_MAP.get(ext)
        if lang:
            langs.add(lang)
    return langs


def run_static_analysis(
    changed_files: list[str],
    root: Path,
    repo_langs: set[str] | None = None,
) -> str | None:
    if not changed_files:
        return None

    detected_langs = repo_langs or _languages_in_files(changed_files)
    blocks: list[str] = []

    for tool in TOOLS:
        tool_langs = set(tool.languages)
        if not (tool_langs & detected_langs or LANG_ANY in tool_langs):
            continue
        if not tool.detect(root):
            continue
        cmd = tool.build_command(changed_files, root)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            logger.warning("Static tool %s not on PATH, skipping", tool.name)
            continue
        except subprocess.TimeoutExpired:
            logger.warning("Static tool %s timed out after 30s, skipping", tool.name)
            continue

        if result.returncode != 0 and not result.stdout.strip() and not result.stderr.strip():
            logger.warning(
                "Static tool %s exited %d with no output, skipping",
                tool.name,
                result.returncode,
            )
            continue

        block = tool.parse_output(result.stdout, result.stderr, root, changed_files)
        if block:
            blocks.append(f"### {tool.name}\n{block}")

    if not blocks:
        return None

    aggregate = "\n\n".join(blocks)
    if len(aggregate) > STATIC_BUDGET:
        included: list[str] = []
        current_len = 0
        for block in blocks:
            sep = "\n\n" if included else ""
            if current_len + len(sep) + len(block) > STATIC_BUDGET:
                break
            included.append(block)
            current_len += len(sep) + len(block)
        omitted = len(blocks) - len(included)
        aggregate = "\n\n".join(included)
        aggregate += f"\n… ({omitted} tool output(s) omitted by static-analysis budget)"
    return aggregate
