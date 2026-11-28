#!/usr/bin/env python3
"""Qualify metadata-only upstream detection markers."""

from pathlib import Path
import os
import subprocess
import tempfile

helper = Path(".github/scripts/upstream-marker.sh").resolve()

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    repository = root / "repository"
    repository.mkdir()

    home = root / "home"
    home.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }

    def execute(*args, input=None):
        return subprocess.run(
            list(map(str, args)),
            cwd=repository,
            env=env,
            input=input,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def git(*args, input=None):
        result = execute("git", *args, input=input)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def marker(*args):
        result = execute("bash", helper, *args)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "--template=", "--initial-branch=main")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")

    (repository / "source.txt").write_text("upstream\n")

    git("add", "source.txt")
    git("commit", "-m", "Upstream release")

    upstream = git("rev-parse", "HEAD")
    created = marker("create", upstream)

    assert created != upstream
    assert git("cat-file", "-t", created) == "commit"
    assert git("ls-tree", "-r", created) == ""
    assert git("rev-list", "--parents", "-n", "1", created) == created

    print("PASS: Marker has no files, workflows or parents.")

    assert marker("matches", upstream, created) == "true"

    print("PASS: Exact upstream identity recognized.")

    assert marker("matches", "a" * 40, created) == "false"

    print("PASS: Different upstream identity rejected.")

    assert marker("matches", upstream, upstream) == "true"

    print("PASS: Legacy source marker remains recognizable.")

    message = f"noctalia-upstream-tablet-v2:v1:{upstream}"

    nonempty_tree = git("rev-parse", "HEAD^{tree}")

    invalid = git(
        "commit-tree", nonempty_tree,
        input=message + "\n",
    )

    assert marker("matches", upstream, invalid) == "false"

    print("PASS: Marker containing source files rejected.")

    empty_tree = git("mktree", input="")

    invalid = git(
        "commit-tree", empty_tree, "-p", upstream,
        input=message + "\n",
    )

    assert marker("matches", upstream, invalid) == "false"

    print("PASS: Marker containing source ancestry rejected.")

    invalid = git(
        "commit-tree", empty_tree,
        input="unrecognized marker format\n",
    )

    assert marker("matches", upstream, invalid) == "false"

    print("PASS: Unexpected marker format rejected.")

    for arguments in (
        ("create", "HEAD"),
        ("create", ""),
        ("create", upstream, "extra"),
        ("matches", upstream),
        ("matches", upstream, "HEAD"),
        ("unknown", upstream),
    ):
        result = execute("bash", helper, *arguments)

        assert result.returncode != 0, arguments
        assert not result.stdout.strip(), arguments

    print("PASS: Six malformed operation cases rejected.")

    assert git("rev-parse", "HEAD") == upstream
    assert git("status", "--porcelain") == ""

    print("PASS: Original branch and working tree unchanged.")

print("PASS: Metadata-only marker qualification.")
print("No network operations or real credentials used.")
