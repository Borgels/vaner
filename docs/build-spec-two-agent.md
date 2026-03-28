# Build Spec: Two-Agent Split

**Status:** Ready to build  
**Builder:** OpenClaw + Claude  
**Target runtime:** LangGraph (local, Ollama / devstral)

This spec is the concrete implementation target for splitting the current single-agent PoC into `repo-analyzer` + `broker`. It defines the artefact store contract, both graph structures, and the wiring between them.

---

## 1. Artefact Store Contract

This is the most important thing to get right. Both agents talk through this store — it's the only interface between them.

### Location

```
~/repos/Vaner/.vaner/cache/
```

Git-ignored. Created on first analyzer run.

### Artefact file format

Each artefact is a single JSON file:

```json
{
  "key": "file_summary:apps/studio-agent/src/agent/graph.py",
  "kind": "file_summary",
  "source_path": "apps/studio-agent/src/agent/graph.py",
  "source_mtime": 1743165600.0,
  "generated_at": 1743165700.0,
  "model": "devstral:latest",
  "content": "...",
  "metadata": {}
}
```

| Field | Description |
|---|---|
| `key` | Unique identifier. Format: `{kind}:{source_path}` |
| `kind` | Artefact type (see kinds below) |
| `source_path` | Repo-relative path this artefact was derived from |
| `source_mtime` | mtime of source file at generation time |
| `generated_at` | Unix timestamp when this artefact was written |
| `model` | Model used to generate |
| `content` | The actual artefact (string) |
| `metadata` | Kind-specific extras (e.g. symbol list, import list) |

### Artefact kinds (start with these three)

| Kind | What it contains |
|---|---|
| `file_summary` | 3–5 sentence plain-English summary of a file's purpose and key exports |
| `dir_summary` | Summary of a directory: what it contains, main entry points |
| `repo_index` | Flat JSON index: `{ path → {kind, summary, mtime} }` for all indexed files |

### File naming

```
.vaner/cache/{kind}/{source_path_urlencoded}.json
```

Example:
```
.vaner/cache/file_summary/apps%2Fstudio-agent%2Fsrc%2Fagent%2Fgraph.py.json
.vaner/cache/dir_summary/apps%2Fstudio-agent%2Fsrc%2Fagent.json
.vaner/cache/repo_index/root.json
```

### Staleness rule

An artefact is **stale** if:
- The source file's current `mtime > artefact.source_mtime`, **or**
- The artefact is older than `MAX_AGE_SECONDS` (default: 3600)

The broker checks staleness before using an artefact. If stale, it falls back to direct file reads (current behavior).

---

## 2. `repo-analyzer` Graph

### Location

```
apps/repo-analyzer/
  src/analyzer/graph.py
  src/analyzer/__init__.py
  pyproject.toml
  langgraph.json
  .env.example
```

### Inputs (State)

```python
@dataclass
class AnalyzerState:
    target_path: str = "."          # repo-relative path to analyze (file or dir)
    force_refresh: bool = False     # ignore staleness, regenerate anyway
    artefacts_written: list[str] = field(default_factory=list)   # keys of written artefacts
    errors: list[str] = field(default_factory=list)
```

### Graph flow

```
__start__
    │
    ▼
discover_targets          # walk target_path, collect files to analyze
    │                     # skip: binaries, .git, __pycache__, .venv, node_modules
    ▼
filter_stale              # check cache for each target, remove fresh ones
    │                     # (unless force_refresh=True)
    │
    ├── nothing to do ──→ END
    │
    ▼
generate_file_summaries   # for each stale file: read + LLM summarize, write to cache
    │                     # batch in groups of 5 to avoid thrashing Ollama
    ▼
generate_dir_summaries    # for each directory that had stale files: aggregate summaries
    │
    ▼
update_repo_index         # rewrite .vaner/cache/repo_index/root.json
    │
    ▼
END
```

### Key implementation notes

**`discover_targets`** — returns a flat list of file paths. Skip anything that:
- matches `.gitignore` patterns
- is in `.vaner/`, `.git/`, `__pycache__/`, `.venv/`, `node_modules/`
- is not valid UTF-8 (binary)
- is larger than 50KB (too large to summarize usefully)

**`generate_file_summaries`** — system prompt for each file:
```
You are summarizing a source file for a developer. 
Given the file path and contents, write 3-5 sentences covering:
- What this file does
- Key exports, classes, or functions
- How it fits into the project (if clear from the code)
Be factual. Do not invent. If the file is trivial (e.g. __init__.py), say so briefly.
```

**`generate_dir_summaries`** — collects all `file_summary` artefacts under a directory and asks the model to synthesize a directory-level summary. Uses `content` from each child artefact, not the raw files.

**`update_repo_index`** — reads all artefacts in the cache and writes a single flat index JSON:
```json
{
  "generated_at": 1743165700.0,
  "files": {
    "apps/studio-agent/src/agent/graph.py": {
      "kind": "file_summary",
      "summary": "...",
      "mtime": 1743165600.0
    }
  }
}
```

### Triggered by

- On demand: user runs the analyzer explicitly
- Eventually: file watcher (inotify/watchdog) — not in this build

---

## 3. `broker` Graph (updated from current PoC)

The current `studio-agent` becomes the broker. Minimal changes — just add cache-read logic before falling back to direct tools.

### New node: `load_context_from_cache`

Insert between `__start__` and `first_model_call`:

```
__start__
    │
    ▼
load_context_from_cache   ← NEW
    │
    ▼
first_model_call
    │
    ...
```

**What it does:**
1. Check if `repo_index` exists and is fresh
2. If yes: load the index, find the top N most relevant file summaries based on simple keyword overlap with `user_input`
3. Inject them into the system prompt as prepended context:
   ```
   ## Pre-loaded context (from cache)
   
   ### apps/studio-agent/src/agent/graph.py
   <summary text>
   
   ### apps/studio-agent/pyproject.toml
   <summary text>
   ```
4. If no cache or all stale: skip silently, fall through to existing direct-read behavior

### Updated State

```python
@dataclass
class State:
    user_input: str = ""
    response: str = ""
    tool_requests: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    cached_context: str = ""        # ← NEW: injected from cache
    cache_hit: bool = False         # ← NEW: whether cache was used
```

### No other changes to broker

The existing tool loop, routing logic, and second model call stay exactly as-is. The cache is purely additive — it prepends context. The agent still has all its tools and can still do direct reads if it needs to go deeper.

---

## 4. Shared utilities

Extract into `libs/vaner-tools/` (simple Python package, no framework):

```
libs/
  vaner-tools/
    src/vaner_tools/
      __init__.py
      repo_tools.py      # list_files, read_file, find_files, grep_text (from current agent)
      artefact_store.py  # read/write/check_staleness for .vaner/cache/
      paths.py           # REPO_ROOT, cache paths, path validation
    pyproject.toml
```

Both `repo-analyzer` and `broker` depend on `vaner-tools`. This avoids duplicating the tool implementations.

---

## 5. Repo layout after this build

```
apps/
  studio-agent/     → rename to broker/ (or keep name, update internals)
  repo-analyzer/    → new

libs/
  vaner-tools/      → new shared package

docs/
  vaner_report.docx
  agent-architecture.md
  build-spec-two-agent.md   ← this file

.vaner/
  cache/            → git-ignored, created at runtime
```

---

## 6. Build order

1. `libs/vaner-tools/` — extract and package the shared tools + artefact store
2. `apps/repo-analyzer/` — new graph, depends on vaner-tools
3. `apps/broker/` (rename studio-agent) — add `load_context_from_cache` node, depends on vaner-tools
4. Test: run analyzer on the repo, then ask the broker a question and confirm it uses cached summaries
5. Commit

---

## 7. Out of scope for this build

- File watcher / auto-trigger
- Synthesizer agent (patch candidates, heavy LLM work) → DGX Spark, next phase
- Eval agent → after synthesizer
- Multi-turn / persistent broker state
- Any write tools for the broker

Keep it small. The goal is a working cache-backed broker, not a full platform.
