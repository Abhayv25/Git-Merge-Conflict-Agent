"""
Cheap first-pass filter: finds which files are touched by more than one
active branch, so merge_check.py only has to run its real (more expensive)
simulation on pairs that could plausibly conflict.
"""

from dataclasses import dataclass
from typing import Dict, List

from .gitcmd import run_git
from .poller import ActiveBranch


@dataclass
class FileOverlap:
    file: str
    branches: List[ActiveBranch]


def get_changed_files(repo_path: str, base_ref: str, head_sha: str) -> List[str]:
    """
    Files changed on head_sha since it diverged from base_ref.

    --no-renames reports a rename as its old path and its new path. With
    rename detection on, git lists only the new path, so a branch that renamed
    a file never overlapped with a branch that edited it under the old name,
    and a real conflict between them was never checked.
    """
    result = run_git(
        repo_path, ["diff", "--name-only", "--no-renames", f"{base_ref}...{head_sha}"]
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def find_overlapping_files(
    repo_path: str, base_ref: str, branches: List[ActiveBranch]
) -> List[FileOverlap]:
    touched_by: Dict[str, List[ActiveBranch]] = {}

    for branch in branches:
        for file in get_changed_files(repo_path, base_ref, branch.head_sha):
            touched_by.setdefault(file, []).append(branch)

    return [
        FileOverlap(file=file, branches=touchers)
        for file, touchers in touched_by.items()
        if len(touchers) >= 2
    ]
