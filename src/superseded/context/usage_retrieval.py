from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

USAGE_BUDGET = 2000
MAX_SYMBOLS = 25

_PYTHON_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w+)|"
    r"^\s*class\s+(\w+)|"
    r"^\s*([A-Z]\w*)\s*=",
    re.MULTILINE,
)

_JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|"
    r"^\s*(?:export\s+)?class\s+(\w+)|"
    r"^\s*(?:export\s+)?const\s+(\w+)\s*=|"
    r"^\s*(?:export\s+)?interface\s+(\w+)|"
    r"^\s*(?:export\s+)?type\s+(\w+)",
    re.MULTILINE,
)

_GO_SYMBOL_RE = re.compile(
    r"^\s*func(?:\s+\([^)]+\))?\s+(\w+)|"
    r"^\s*type\s+(\w+)\s+struct|"
    r"^\s*type\s+(\w+)\s+interface|"
    r"^\s*var\s+(\w+)|"
    r"^\s*const\s+(\w+)",
    re.MULTILINE,
)

_GENERIC_RE = re.compile(r"\b([A-Za-z_]\w{3,})\b")

_KEYWORDS = frozenset(
    {
        "self",
        "cls",
        "return",
        "if",
        "else",
        "for",
        "while",
        "import",
        "from",
        "const",
        "let",
        "var",
        "func",
        "type",
        "package",
        "struct",
        "interface",
        "def",
        "class",
        "async",
        "await",
        "yield",
        "with",
        "as",
        "try",
        "except",
        "finally",
        "raise",
        "pass",
        "break",
        "continue",
        "elif",
        "lambda",
        "not",
        "and",
        "or",
        "in",
        "is",
        "None",
        "True",
        "False",
        "print",
        "this",
        "new",
        "delete",
        "typeof",
        "instanceof",
        "void",
    }
)

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".ts": "ts",
    ".tsx": "ts",
    ".go": "go",
}


def extract_symbols(diff: str, lang: str) -> list[str]:
    """Extract changed symbol names from added diff lines."""
    added_lines = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    if lang == "python":
        matches = _PYTHON_SYMBOL_RE.finditer(added_lines)
    elif lang in ("js", "ts"):
        matches = _JS_SYMBOL_RE.finditer(added_lines)
    elif lang == "go":
        matches = _GO_SYMBOL_RE.finditer(added_lines)
    else:
        matches = _GENERIC_RE.finditer(added_lines)

    seen: set[str] = set()
    symbols: list[str] = []
    for m in matches:
        groups = m.groups()
        name = next((g for g in groups if g is not None), None)
        if name and name not in _KEYWORDS and name not in seen:
            seen.add(name)
            symbols.append(name)
            if len(symbols) >= MAX_SYMBOLS:
                break

    return symbols


def retrieve_usages(diff: str, root: Path) -> str | None:
    """Find callers of changed symbols via ripgrep."""
    files_match = re.search(r"^diff --git a/(.+?) b/", diff, re.MULTILINE)
    changed_file = files_match.group(1) if files_match else None

    lang = _LANG_MAP.get(Path(changed_file).suffix) if changed_file else "python"

    if not lang:
        return None

    symbols = extract_symbols(diff, lang)
    if not symbols:
        return None

    blocks: list[str] = []
    total_chars = 0

    for sym in symbols:
        glob_args = [
            "--glob",
            "!.venv/**",
            "--glob",
            "!node_modules/**",
            "--glob",
            "!.git/**",
            "--glob",
            "!*.lock",
        ]
        if changed_file:
            glob_args += ["--glob", f"!{changed_file}"]

        try:
            result = subprocess.run(
                ["rg", "-n", "--max-count", "4", rf"\b{sym}\b", str(root), *glob_args],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError, subprocess.TimeoutExpired:
            logger.warning("ripgrep not on PATH or timed out, skipping usage retrieval")
            return None

        if result.returncode == 0 and result.stdout.strip():
            block = f"### Usages of `{sym}`\n{result.stdout.strip()}"
            if total_chars + len(block) > USAGE_BUDGET:
                omitted = len(symbols) - len(blocks)
                blocks.append(f"\u2026 ({omitted} more usages omitted by retrieval budget)")
                break
            blocks.append(block)
            total_chars += len(block)

    if not blocks:
        return None
    return "\n\n".join(blocks)
