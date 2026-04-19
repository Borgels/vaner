# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from pathlib import Path

from vaner.intent.adapter import RelationshipEdge

_JS_IMPORT = re.compile(r"""import\s+.+?\s+from\s+["']([^"']+)["']""")
_JS_REQUIRE = re.compile(r"""require\(["']([^"']+)["']\)""")
_GO_IMPORT = re.compile(r'^\s*"([^"]+)"', re.MULTILINE)
_RUST_USE = re.compile(r"""^\s*(?:use|mod)\s+([a-zA-Z0-9_:]+)""", re.MULTILINE)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _python_import_targets(path: Path, repo_root: Path) -> list[str]:
    text = _safe_read(path)
    if not text.strip():
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    rel = path.relative_to(repo_root)
    module_parts = list(rel.with_suffix("").parts)
    if path.name == "__init__.py":
        module_parts = list(rel.parent.parts)

    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            level = max(0, node.level)
            base_parts = module_parts if path.name == "__init__.py" else module_parts[:-1]
            if level == 0:
                base_parts = []
            if level > 0:
                trim = max(0, level - 1)
                if trim >= len(base_parts):
                    base_parts = []
                else:
                    base_parts = base_parts[: len(base_parts) - trim]

            if node.module:
                targets.append(".".join([*base_parts, *node.module.split(".")]))
            else:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    targets.append(".".join([*base_parts, alias.name]))
    resolved: list[str] = []
    candidate_roots = [repo_root]
    src_root = repo_root / "src"
    if src_root.exists():
        candidate_roots.append(src_root)
    for module in targets:
        module_path = module.replace(".", "/")
        for root in candidate_roots:
            py_candidate = root / f"{module_path}.py"
            pkg_candidate = root / module_path / "__init__.py"
            candidate: Path | None = None
            if py_candidate.exists():
                candidate = py_candidate
            elif pkg_candidate.exists():
                candidate = pkg_candidate
            if candidate is not None:
                resolved.append(str(candidate.relative_to(repo_root)))
                break
    return resolved


def _text_import_targets(text: str, suffix: str) -> list[str]:
    if suffix in {".js", ".ts", ".jsx", ".tsx"}:
        return _JS_IMPORT.findall(text) + _JS_REQUIRE.findall(text)
    if suffix == ".go":
        return _GO_IMPORT.findall(text)
    if suffix == ".rs":
        return _RUST_USE.findall(text)
    return []


def extract_code_relationship_edges(repo_root: Path) -> list[RelationshipEdge]:
    edges: list[RelationshipEdge] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        if any(part in {".git", ".venv", "__pycache__", ".vaner"} for part in path.relative_to(repo_root).parts):
            continue

        if path.suffix == ".py":
            targets = _python_import_targets(path, repo_root)
        else:
            targets = _text_import_targets(_safe_read(path), path.suffix)

        for target in targets:
            if target.startswith("."):
                # Relative JS/TS imports.
                resolved = (path.parent / target).with_suffix(path.suffix)
                if resolved.exists():
                    normalized_resolved = str(resolved.relative_to(repo_root)).replace("\\", "/")
                    target_key = f"file:{normalized_resolved}"
                else:
                    continue
            elif (repo_root / target).exists():
                normalized_target = target.replace("\\", "/")
                target_key = f"file:{normalized_target}"
            else:
                continue
            edges.append(RelationshipEdge(source_key=f"file:{rel}", target_key=target_key, kind="imports"))
    return edges


class RelationshipGraph:
    def __init__(self, edges: list[RelationshipEdge]) -> None:
        self.edges = edges
        self.forward: dict[str, set[str]] = defaultdict(set)
        self.reverse: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            self.forward[edge.source_key].add(edge.target_key)
            self.reverse[edge.target_key].add(edge.source_key)

    def propagate(self, source_key: str, depth: int = 2) -> list[str]:
        seen = {source_key}
        queue: deque[tuple[str, int]] = deque([(source_key, 0)])
        while queue:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for neighbor in self.reverse.get(current, set()) | self.forward.get(current, set()):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append((neighbor, level + 1))
        seen.remove(source_key)
        return sorted(seen)

    def clusters(self, min_size: int = 2) -> list[list[str]]:
        remaining = set(self.forward.keys()) | set(self.reverse.keys())
        clusters: list[list[str]] = []
        while remaining:
            root = remaining.pop()
            group = {root}
            queue = deque([root])
            while queue:
                current = queue.popleft()
                for neighbor in self.forward.get(current, set()) | self.reverse.get(current, set()):
                    if neighbor in group:
                        continue
                    group.add(neighbor)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                    queue.append(neighbor)
            if len(group) >= min_size:
                clusters.append(sorted(group))
        clusters.sort(key=len, reverse=True)
        return clusters
