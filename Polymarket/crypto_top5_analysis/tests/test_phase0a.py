#!/usr/bin/env python3
"""Offline, standard-library-only acceptance checks for Phase 0A."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEEDS = ROOT / "config" / "target_seeds.yaml"
FORBIDDEN_SEED = ROOT / "config" / "targets.seed.yaml"
FULL_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")


def seed_blocks(text: str) -> list[str]:
    return re.findall(r"(?ms)^  - seed_rank:.*?(?=^  - seed_rank:|\Z)", text)


def ignored(path: str, patterns: list[str]) -> bool:
    matched = False
    for raw in patterns:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]
        directory_pattern = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        candidates = (path, path.rstrip("/"), Path(path).name)
        if (
            any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates)
            or (directory_pattern and path.startswith(pattern + "/"))
        ):
            matched = not negate
    return matched


def main() -> None:
    assert SEEDS.is_file(), "the canonical seed file is missing"
    assert not FORBIDDEN_SEED.exists(), "the misspelled seed file must not exist"

    text = SEEDS.read_text(encoding="utf-8")
    blocks = seed_blocks(text)
    assert len(blocks) == 5, f"expected 5 seed accounts, found {len(blocks)}"

    ranks = [int(re.search(r"seed_rank: (\d+)", block).group(1)) for block in blocks]
    assert ranks == [1, 2, 3, 4, 5], f"unexpected seed ranks: {ranks}"

    abbreviated = [
        re.search(r'abbreviated_address: "([^"]+)"', block).group(1)
        for block in blocks
    ]
    assert len(set(abbreviated)) == 5, "abbreviated addresses must be unique"
    assert not FULL_ADDRESS.search(text), "seed file contains a full wallet address"
    assert all("address_status: unresolved" in block for block in blocks)

    public = ROOT / "public"
    private_names = {".env", "auth.json"}
    private_suffixes = {
        ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".gz", ".parquet"
    }
    for path in public.rglob("*"):
        assert not path.is_symlink(), f"public symlink is forbidden: {path}"
        if path.is_file():
            assert path.name not in private_names, f"private file in public: {path}"
            assert not any(
                path.name.endswith(suffix) for suffix in private_suffixes
            ), f"private artifact in public: {path}"

    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for path in (
        ".env",
        "data/example.json",
        "logs/example.log",
        "sample.db",
        "sample.sqlite",
        "sample.sqlite3",
        "sample.db-wal",
        "sample.db-shm",
        "__pycache__/module.pyc",
        ".pytest_cache/state",
        ".venv/bin/python",
    ):
        assert ignored(path, patterns), f"expected gitignore coverage for {path}"
    assert not ignored("public/index.html", patterns), "public/ must not be ignored"

    for directory in (ROOT / "data", ROOT / "logs"):
        assert (directory.stat().st_mode & 0o777) == 0o700, (
            f"{directory.name}/ must have mode 700"
        )

    assert not FULL_ADDRESS.search(
        "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (
                ROOT / "AGENTS.md",
                ROOT / "README.md",
                ROOT / "docs" / "DATA_SOURCES.md",
                ROOT / "docs" / "PROGRESS.md",
            )
        )
    ), "Phase 0A documents contain a full wallet address"

    print("Phase 0A checks passed")


if __name__ == "__main__":
    main()
