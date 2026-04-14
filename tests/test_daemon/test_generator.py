# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.daemon.engine.generator import generate_artefact, generate_diff_summary


def test_generate_artefact_for_normal_file(temp_repo):
    source = temp_repo / "normal.py"
    source.write_text(
        "MAX_ITEMS = 10\n\n"
        "class Service:\n"
        "    pass\n\n"
        "def f(limit: int = 5):\n"
        "    return list(range(limit))[:MAX_ITEMS]\n",
        encoding="utf-8",
    )
    artefact = generate_artefact(source, temp_repo)
    assert artefact.source_path == "normal.py"
    assert "Functions:" in artefact.content
    assert "Constants:" in artefact.content
    assert "Limits:" in artefact.content


def test_generate_artefact_for_empty_file(temp_repo):
    source = temp_repo / "empty.py"
    source.write_text("\n", encoding="utf-8")
    artefact = generate_artefact(source, temp_repo)
    assert artefact.content == "Empty or whitespace-only file."


def test_generate_artefact_for_binary_file(temp_repo):
    source = temp_repo / "binary.bin"
    source.write_bytes(b"\x00\xff\x10\x11")
    artefact = generate_artefact(source, temp_repo)
    assert artefact.source_path == "binary.bin"


def test_generate_diff_summary_redacts_patterns(temp_repo):
    diff = "+ password = secret123"
    artefact = generate_diff_summary(
        temp_repo,
        "sample.py",
        diff,
        redact_patterns=[r"secret\d+"],
    )
    assert "REDACTED" in artefact.content
