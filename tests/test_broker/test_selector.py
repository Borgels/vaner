# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

from vaner.broker.selector import select_artefacts
from vaner.models.artefact import Artefact, ArtefactKind


def test_select_artefacts_prefers_prompt_matches():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="authentication and login flow",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="image rendering only",
        ),
    ]
    selected = select_artefacts("explain authentication", artefacts, top_n=1)
    assert selected[0].key == "file_summary:a.py"


def test_select_artefacts_prefers_git_and_working_set_matches():
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="generic content",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=time.time(),
            generated_at=time.time(),
            model="test",
            content="generic content",
        ),
    ]
    selected = select_artefacts(
        "explain flow",
        artefacts,
        top_n=1,
        preferred_paths={"b.py"},
        preferred_keys={"file_summary:b.py"},
    )
    assert selected[0].key == "file_summary:b.py"
