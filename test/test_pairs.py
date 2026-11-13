"""
Two PRs that conflict in more than one file should be checked once and
announced once, with every conflicting file listed in that one message.
"""

import subprocess

from src.checker import run_check
from src.poller import ActiveBranch
from src.state import FileStateStore


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _two_file_conflict_repo(path):
    repo = str(path)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    for name in ("a.txt", "b.txt"):
        (path / name).write_text("original\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")

    for branch, text in (("left", "left\n"), ("right", "right\n")):
        _git(repo, "checkout", "-q", "-b", branch, "main")
        for name in ("a.txt", "b.txt"):
            (path / name).write_text(text)
        _git(repo, "commit", "-qam", branch)
    _git(repo, "checkout", "-q", "main")

    return repo, [
        ActiveBranch("left", "dev", 1, _git(repo, "rev-parse", "left")),
        ActiveBranch("right", "dev", 2, _git(repo, "rev-parse", "right")),
    ]


def test_one_check_and_one_alert_per_branch_pair(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo, branches = _two_file_conflict_repo(repo_dir)
    sent = []

    summary = run_check(repo, "main", branches, FileStateStore(str(tmp_path / "s.json")), sent.append)

    assert summary.overlapping_files == 2
    assert summary.pairs_checked == 1
    assert summary.notifications_sent == 1
    assert "a.txt" in sent[0] and "b.txt" in sent[0]


def test_resolved_conflict_alerts_again_if_it_comes_back(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo, (left, right) = _two_file_conflict_repo(repo_dir)
    state = str(tmp_path / "s.json")
    sent = []

    run_check(repo, "main", [left, right], FileStateStore(state), sent.append)
    assert len(sent) == 1

    # The right branch resolves the conflict by taking the left side.
    _git(repo, "checkout", "-q", "right")
    for name in ("a.txt", "b.txt"):
        (repo_dir / name).write_text("left\n")
    _git(repo, "commit", "-qam", "resolve")
    right = ActiveBranch("right", "dev", 2, _git(repo, "rev-parse", "HEAD"))

    resolved = run_check(repo, "main", [left, right], FileStateStore(state), sent.append)
    assert resolved.resolved == 1
    assert len(sent) == 1

    # A later push brings the conflict back, and the team hears about it.
    (repo_dir / "a.txt").write_text("right again\n")
    _git(repo, "commit", "-qam", "conflict again")
    _git(repo, "checkout", "-q", "main")
    right = ActiveBranch("right", "dev", 2, _git(repo, "rev-parse", "right"))

    again = run_check(repo, "main", [left, right], FileStateStore(state), sent.append)
    assert again.notifications_sent == 1
    assert len(sent) == 2
