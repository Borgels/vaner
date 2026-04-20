# SPDX-License-Identifier: Apache-2.0

from importlib.metadata import PackageNotFoundError, version

from vaner._version import VERSION
from vaner.api import forget, inspect, inspect_last, precompute, predict, prepare, query
from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter, CorpusAdapter

__all__ = [
    "VERSION",
    "__version__",
    "prepare",
    "query",
    "predict",
    "precompute",
    "inspect",
    "inspect_last",
    "forget",
    "VanerEngine",
    "CorpusAdapter",
    "CodeRepoAdapter",
]

try:
    __version__ = version("vaner")
except PackageNotFoundError:  # pragma: no cover - editable installs fallback
    __version__ = VERSION
