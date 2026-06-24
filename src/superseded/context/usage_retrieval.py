from __future__ import annotations

import keyword
import logging
import re
import subprocess
from pathlib import Path

from superseded.diff import parse_diff_files

logger = logging.getLogger(__name__)

USAGE_BUDGET = 6000
MAX_SYMBOLS = 25

_PYTHON_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+(\w+)|"
    r"^\s*class\s+(\w+)|"
    r"^\s*([A-Z]\w*)\s*=|"
    r"^\s*(\w+)\s*:\s*",
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
    set(keyword.kwlist)
    | {
        "self",
        "cls",
        "print",
        "this",
        "new",
        "delete",
        "typeof",
        "instanceof",
        "void",
        "func",
        "package",
        "struct",
        "interface",
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
        primary_re = _PYTHON_SYMBOL_RE
    elif lang in ("js", "ts"):
        primary_re = _JS_SYMBOL_RE
    elif lang == "go":
        primary_re = _GO_SYMBOL_RE
    else:
        primary_re = _GENERIC_RE

    seen: set[str] = set()
    symbols: list[str] = []

    def add(name: str | None) -> None:
        if name and name not in _KEYWORDS and name not in seen:
            seen.add(name)
            symbols.append(name)

    for m in primary_re.finditer(added_lines):
        name = next((g for g in m.groups() if g is not None), None)
        add(name)

    if primary_re is not _GENERIC_RE:
        for m in _GENERIC_RE.finditer(added_lines):
            add(m.group(1))

    return symbols[-MAX_SYMBOLS:]


def retrieve_usages(diff: str, root: Path) -> str | None:
    """Find callers of changed symbols via ripgrep."""
    entries = parse_diff_files(diff)

    seen: set[str] = set()
    symbols: list[str] = []

    if entries:
        changed_files = [e["file"] for e in entries]
        for entry in entries:
            lang = _LANG_MAP.get(Path(entry["file"]).suffix)
            if not lang:
                continue
            for sym in extract_symbols(entry["diff"], lang):
                if sym not in seen:
                    seen.add(sym)
                    symbols.append(sym)
        symbols = symbols[-MAX_SYMBOLS:]
    else:
        changed_files = []
        symbols = extract_symbols(diff, "python")

    if not symbols:
        return None

    exclude_globs = [
        "--glob",
        "!.venv/**",
        "--glob",
        "!node_modules/**",
        "--glob",
        "!.git/**",
        "--glob",
        "!*.lock",
    ]
    for cf in changed_files:
        exclude_globs += ["--glob", f"!{cf}"]

    alternation = "|".join(re.escape(s) for s in symbols)
    pattern = rf"\b({alternation})\b"

    try:
        result = subprocess.run(
            ["rg", "-n", "--max-count", "4", pattern, str(root), *exclude_globs],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        logger.warning("ripgrep not on PATH, skipping usage retrieval")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out for batched symbols, skipping")
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    lines = result.stdout.strip().splitlines()
    symbol_lines: dict[str, list[str]] = {s: [] for s in symbols}
    for line in lines:
        for sym in symbols:
            if re.search(rf"\b{re.escape(sym)}\b", line):
                symbol_lines[sym].append(line)
                break

    blocks: list[str] = []
    total_chars = 0
    for sym in symbols:
        sym_lines = symbol_lines[sym]
        if not sym_lines:
            continue
        block = f"### Usages of `{sym}`\n" + "\n".join(sym_lines)
        if total_chars + len(block) > USAGE_BUDGET:
            omitted = len(symbols) - len(blocks)
            blocks.append(f"\u2026 ({omitted} more usages omitted by retrieval budget)")
            break
        blocks.append(block)
        total_chars += len(block)

    if not blocks:
        return None
    return "\n\n".join(blocks)
