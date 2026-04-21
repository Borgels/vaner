# SPDX-License-Identifier: Apache-2.0
"""Shared cockpit UI package.

This package owns the FastAPI application factory that serves the React
cockpit and its common control APIs. Daemon, proxy, and MCP surfaces all
mount the same factory so the user experience is identical regardless of
which ``vaner`` entrypoint they launched.
"""

from vaner.ui.server import build_cockpit_app, cockpit_bundle_sha, cockpit_dist_dir

__all__ = ["build_cockpit_app", "cockpit_dist_dir", "cockpit_bundle_sha"]
