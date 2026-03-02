"""Crucible test sharding — partition tests across N shards for parallel execution.

Ports ``crucible-shard-partition.sh``.  CRC32 hashing assigns each test to a
deterministic shard.  With historical durations, greedy LPT balances load.
"""
from __future__ import annotations

import binascii
from dataclasses import dataclass
from pathlib import Path

from dark_factory.crucible.orchestrator import CrucibleResult, CrucibleVerdict


def _crc32_shard(name: str, num_shards: int) -> int:
    """Return 0-based shard index for *name* using CRC32 hash."""
    h = binascii.crc32(name.encode()) & 0xFFFFFFFF
    return h % num_shards


def partition_tests(
    test_files: list[Path],
    num_shards: int,
    *,
    durations: dict[str, float] | None = None,
) -> list[list[Path]]:
    """Split *test_files* across *num_shards* balanced partitions.

    With *durations* (file→estimated ms), uses greedy LPT for balance.
    Without, deterministic CRC32 hashing (mirrors the bash implementation).
    """
    if num_shards < 1:
        raise ValueError("num_shards must be >= 1")

    shards: list[list[Path]] = [[] for _ in range(num_shards)]

    if not test_files:
        return shards

    if num_shards == 1:
        shards[0] = list(test_files)
        return shards

    if durations:
        return _partition_by_duration(test_files, num_shards, durations)

    # Deterministic CRC32 hash partitioning (parity with bash)
    for f in test_files:
        idx = _crc32_shard(str(f), num_shards)
        shards[idx].append(f)
    return shards


def _partition_by_duration(
    test_files: list[Path],
    num_shards: int,
    durations: dict[str, float],
) -> list[list[Path]]:
    """Greedy LPT partitioning: assign longest-first to the lightest shard."""
    shards: list[list[Path]] = [[] for _ in range(num_shards)]
    loads: list[float] = [0.0] * num_shards

    def _dur(p: Path) -> float:
        return durations.get(str(p), durations.get(p.name, 0.0))

    for f in sorted(test_files, key=_dur, reverse=True):
        lightest = min(range(num_shards), key=lambda i: loads[i])
        shards[lightest].append(f)
        loads[lightest] += _dur(f)
    return shards


@dataclass(frozen=True, slots=True)
class ShardResult:
    """Outcome of a single shard's test run."""
    shard_index: int
    result: CrucibleResult


def merge_verdicts(shard_results: list[ShardResult]) -> CrucibleVerdict:
    """Combine per-shard verdicts into one unified verdict.

    * Any ``NO_GO`` → ``NO_GO``
    * No failures but any ``NEEDS_LIVE`` → ``NEEDS_LIVE``
    * Otherwise → ``GO``
    """
    if not shard_results:
        return CrucibleVerdict.NO_GO

    has_needs_live = False
    for sr in shard_results:
        if sr.result.verdict is CrucibleVerdict.NO_GO:
            return CrucibleVerdict.NO_GO
        if sr.result.verdict is CrucibleVerdict.NEEDS_LIVE:
            has_needs_live = True
    return CrucibleVerdict.NEEDS_LIVE if has_needs_live else CrucibleVerdict.GO
