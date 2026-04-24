# SPDX-License-Identifier: Apache-2.0
"""WS7 — branch-name → goal-hint parser.

The lowest-effort signal for a workspace goal is the current git branch
name. ``feat/jwt-migration`` tells us the user is working on a JWT
migration; ``bugfix/auth-token-leak`` tells us they're fixing an auth
leak. This module turns such names into :class:`GoalHint` records the
engine can turn into :class:`WorkspaceGoal` entries.

The grammar is intentionally simple — a small allow-list of common
prefixes (feat, fix, bugfix, chore, refactor, ...) and a heuristic for
splitting the trailing slug into words. More sophisticated inference
(commit clustering, query clustering) lives in separate modules.
"""

from __future__ import annotations

from dataclasses import dataclass

# Well-known branch prefixes and the semantic category they hint at. The
# confidence values reflect how reliably each prefix names a *goal* (not
# just a change topic): a ``feat/`` branch almost always names a
# user-level aspiration; a ``chore/`` branch is usually tooling and
# rarely a user goal.
_PREFIX_HINTS: dict[str, tuple[str, float]] = {
    "feat": ("feature", 0.80),
    "feature": ("feature", 0.80),
    "fix": ("fix", 0.70),
    "bugfix": ("fix", 0.70),
    "hotfix": ("fix", 0.70),
    "refactor": ("refactor", 0.65),
    "refac": ("refactor", 0.65),
    "chore": ("chore", 0.40),
    "docs": ("docs", 0.45),
    "doc": ("docs", 0.45),
    "test": ("tests", 0.55),
    "tests": ("tests", 0.55),
    "perf": ("performance", 0.65),
    "experiment": ("research", 0.55),
    "exp": ("research", 0.55),
    "spike": ("research", 0.55),
    "wip": ("wip", 0.30),
}

_BOILERPLATE_BRANCHES = {"main", "master", "develop", "dev", "trunk", "staging", "production", "prod"}

# Small allow-list of well-known technical acronyms that should render in
# uppercase even when the branch name lowercases them. Keeping it short and
# conservative — anything not on this list follows the regular title-casing
# rule. Extend here if specific acronyms appear in your branch naming
# conventions.
_KNOWN_ACRONYMS: frozenset[str] = frozenset(
    {
        "api",
        "cli",
        "ci",
        "cd",
        "cdn",
        "cors",
        "csrf",
        "css",
        "db",
        "dns",
        "dto",
        "eol",
        "etl",
        "grpc",
        "gui",
        "hal",
        "html",
        "http",
        "https",
        "ide",
        "iot",
        "ipfs",
        "jit",
        "json",
        "jvm",
        "jwt",
        "kms",
        "kpi",
        "kv",
        "llm",
        "mcp",
        "mvc",
        "mvp",
        "nlp",
        "orm",
        "oss",
        "otp",
        "pr",
        "rbac",
        "rest",
        "rpc",
        "saas",
        "sdk",
        "sql",
        "ssh",
        "ssl",
        "sso",
        "tcp",
        "tls",
        "ttl",
        "ui",
        "url",
        "uuid",
        "vm",
        "vpn",
        "xml",
        "xss",
        "yaml",
    }
)


@dataclass(frozen=True, slots=True)
class GoalHint:
    """A branch-name-derived goal suggestion.

    ``title`` is the human-readable rendering of the slug (``"JWT
    migration"``). ``category`` is the prefix's semantic label
    (``"feature"`` / ``"fix"`` / ...). ``slug`` is the raw trailing
    segment after the prefix, kept for downstream deduplication.
    ``confidence`` reflects how reliably this prefix names a user goal.
    """

    title: str
    slug: str
    category: str
    confidence: float


def parse_branch_name(branch: str) -> GoalHint | None:
    """Turn a branch name into a :class:`GoalHint` or return None when the
    name doesn't carry actionable signal.

    Returns None for:
      - empty / whitespace-only branch names
      - default / staging branches (``main``, ``master``, ``develop``, …)
      - branches without a recognisable prefix (we'd rather say "no
        opinion" than invent one)

    Examples (see tests for the full grid)::

        parse_branch_name("feat/jwt-migration")
        # → GoalHint(title="JWT migration", slug="jwt-migration",
        #            category="feature", confidence=0.80)

        parse_branch_name("main")               # → None
        parse_branch_name("random-typo")        # → None
        parse_branch_name("user/abo/feat/...")  # → parses the first
        #     recognised prefix segment it finds from the left.
    """
    if not branch:
        return None
    name = branch.strip()
    if not name:
        return None
    if name.lower() in _BOILERPLATE_BRANCHES:
        return None

    segments = [segment for segment in name.replace("\\", "/").split("/") if segment]
    if not segments:
        return None

    # Walk segments left-to-right; the first one that matches a known
    # prefix "wins". Multi-segment prefixes like "user/abo/feat/..." need
    # us to look past personal namespaces. We stop at the first hit and
    # treat everything after it as the slug.
    for idx, segment in enumerate(segments):
        hint = _PREFIX_HINTS.get(segment.lower())
        if hint is None:
            continue
        category, confidence = hint
        tail_segments = segments[idx + 1 :]
        if not tail_segments:
            # "feat" alone isn't a goal — no slug to title.
            return None
        slug = "/".join(tail_segments)
        title = _slug_to_title(slug)
        if not title:
            return None
        return GoalHint(
            title=title,
            slug=slug,
            category=category,
            confidence=confidence,
        )
    return None


def _slug_to_title(slug: str) -> str:
    """Render a dash/underscore-separated slug into a human-readable title.

    Rules:
      - Split on ``-`` / ``_`` / ``/``.
      - Preserve all-uppercase input words (2-5 chars) as-is — ``JWT``,
        ``RBAC``, ``API``, ``HTTP`` stay uppercase.
      - Lowercase input words get title-cased only when they're the
        first word of the title; trailing words stay lowercase.
      - Non-alphabetic tokens (``"v2"``, numbers) pass through as-is.

    Examples:
      - ``"jwt-migration"``    → ``"JWT migration"``
      - ``"auth_token_leak"``  → ``"Auth token leak"``
      - ``"api-reference"``    → ``"Api reference"``
      - ``"RBAC-for-admin"``   → ``"RBAC for admin"``
    """
    if not slug:
        return ""
    parts: list[str] = []
    for raw in slug.replace("/", " ").split():
        for sub in raw.replace("_", "-").split("-"):
            if sub:
                parts.append(sub)
    if not parts:
        return ""

    def _render(word: str, first: bool) -> str:
        # Known acronyms always render uppercase regardless of input case.
        if word.isalpha() and word.lower() in _KNOWN_ACRONYMS:
            return word.upper()
        # Input was all-uppercase and short → likely an acronym not on
        # the allow-list; keep it upper so we don't downcase something
        # the user clearly meant as an acronym.
        if word.isalpha() and word.isupper() and 2 <= len(word) <= 5:
            return word
        # Mixed / lowercase input. Title-case only the first word.
        if first and word[:1].isalpha():
            return word[:1].upper() + word[1:].lower()
        if word.isalpha():
            return word.lower()
        return word

    rendered = [_render(parts[0], first=True)]
    for word in parts[1:]:
        rendered.append(_render(word, first=False))
    return " ".join(rendered).strip()
