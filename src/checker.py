"""
One full pass over a repository: narrow active branches to the files more than
one of them touched, check each contested pair for a real conflict, and notify
on anything new. Shared by the command-line entry point (main.py) and the AWS
Lambda entry point (lambda_handler.py), which differ only in where the
repository, secrets, and state come from.
"""

import itertools
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Tuple

from .merge_check import check_merge_conflict
from .notifier import CollisionNotification, build_message
from .overlap import FileOverlap, find_overlapping_files
from .poller import ActiveBranch
from .state import StateStore, build_collision_key


@dataclass
class RunSummary:
    active_branches: int = 0
    overlapping_files: int = 0
    pairs_checked: int = 0
    conflicts_found: int = 0
    notifications_sent: int = 0
    skipped_already_notified: int = 0
    errors: int = 0
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _candidate_pairs(overlaps: List[FileOverlap]) -> List[Tuple[ActiveBranch, ActiveBranch]]:
    """
    Every branch pair that shares at least one changed file, each pair once.
    Two PRs that both touch three files are still one merge to simulate, so
    merge-tree runs once per pair rather than once per shared file.
    """
    pairs: Dict[Tuple[int, int], Tuple[ActiveBranch, ActiveBranch]] = {}
    for overlap in overlaps:
        for branch_a, branch_b in itertools.combinations(overlap.branches, 2):
            first, second = sorted([branch_a, branch_b], key=lambda b: b.pr_number)
            pairs.setdefault((first.pr_number, second.pr_number), (first, second))
    return list(pairs.values())


def run_check(
    repo_path: str,
    base_ref: str,
    branches: List[ActiveBranch],
    store: StateStore,
    notify: Callable[[str], None],
    use_llm_messages: bool = False,
) -> RunSummary:
    started = time.monotonic()
    summary = RunSummary(active_branches=len(branches))

    if len(branches) >= 2:
        overlaps = find_overlapping_files(repo_path, base_ref, branches)
        summary.overlapping_files = len(overlaps)

        for branch_a, branch_b in _candidate_pairs(overlaps):
            key = build_collision_key(branch_a.branch_name, branch_b.branch_name)
            try:
                if store.has_been_notified(key):
                    summary.skipped_already_notified += 1
                    continue

                summary.pairs_checked += 1
                result = check_merge_conflict(repo_path, branch_a, branch_b)
                if not result.has_conflict:
                    continue
                summary.conflicts_found += 1

                if not store.claim(key):
                    summary.skipped_already_notified += 1
                    continue

                message = build_message(
                    CollisionNotification(branch_a, branch_b, result), use_llm_messages
                )
                try:
                    notify(message)
                except Exception:
                    store.release(key)  # try again next run instead of going silent
                    raise
                summary.notifications_sent += 1
            except Exception as e:
                summary.errors += 1
                print(f"Skipping {branch_a.branch_name} <-> {branch_b.branch_name}: {e}")

    store.flush()
    summary.duration_ms = int((time.monotonic() - started) * 1000)
    return summary
