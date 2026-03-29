"""Symbol tracer — extracts class/function signatures from Python source files."""
from __future__ import annotations

import ast
import logging
import time
from pathlib import Path

from vaner_tools.artefact_store import Artefact, write_artefact

logger = logging.getLogger("vaner.symbol_tracer")


def _extract_symbols(source: str) -> list[dict]:
    """Parse Python source and extract top-level symbols with signatures."""
    symbols: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in ast.walk(node)
                if isinstance(n, ast.FunctionDef) and n.col_offset > 0
            ]
            symbols.append({
                "kind": "class",
                "name": node.name,
                "line": node.lineno,
                "methods": methods[:20],
            })
        elif isinstance(node, ast.FunctionDef) and node.col_offset == 0:
            args = [a.arg for a in node.args.args]
            symbols.append({
                "kind": "function",
                "name": node.name,
                "line": node.lineno,
                "args": args,
            })

    return symbols


def _format_symbols(symbols: list[dict]) -> str:
    lines = []
    for s in symbols:
        if s["kind"] == "class":
            methods = ", ".join(s["methods"]) if s["methods"] else "—"
            lines.append(f"class {s['name']} (line {s['line']}) → methods: {methods}")
        else:
            args = ", ".join(s["args"])
            lines.append(f"def {s['name']}({args}) (line {s['line']})")
    return "\n".join(lines)


def generate_symbol_trace(
    source_path: Path,
    repo_root: Path,
) -> Artefact | None:
    """Extract symbol trace from a Python file (sync, no model needed)."""
    if source_path.suffix != ".py":
        return None
    try:
        source = source_path.read_text(errors="replace")
        rel_path = str(source_path.relative_to(repo_root))
        symbols = _extract_symbols(source)
        if not symbols:
            return None
        content = _format_symbols(symbols)
        artefact = Artefact(
            key=f"symbol_trace:{rel_path}",
            kind="symbol_trace",
            source_path=rel_path,
            source_mtime=source_path.stat().st_mtime,
            generated_at=time.time(),
            model="ast",  # no model — pure AST extraction
            content=content,
        )
        write_artefact(artefact)
        logger.debug("Generated symbol_trace for %s (%d symbols)", rel_path, len(symbols))
        return artefact
    except Exception as exc:
        logger.error("Failed symbol_trace for %s: %s", source_path, exc)
        return None


def generate_symbol_traces_for_dir(
    directory: Path,
    repo_root: Path,
) -> list[Artefact]:
    """Generate symbol traces for all .py files in a directory."""
    results = []
    for path in sorted(directory.rglob("*.py")):
        parts = path.parts
        if any(p in parts for p in (".venv", "__pycache__", ".vaner", "dist", "build")):
            continue
        a = generate_symbol_trace(path, repo_root)
        if a:
            results.append(a)
    return results
