#!/usr/bin/env python3
# W01-C automation regression qualification.
# Uses disposable repositories and synthetic credentials.
# Qualification source: W01-C1.13f.1-rebase-qualification.txt

from pathlib import Path
import os
import re
import subprocess
import tempfile

helper = Path(
    ".github/scripts/prepare-candidate.sh"
).resolve()

with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)
    repository = root / "repository"
    repository.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": scratch,
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }

    def git(*args):
        return subprocess.check_output(
            ["git", *args],
            cwd=repository,
            env=env,
            text=True,
            stderr=subprocess.PIPE,
            timeout=60,
        ).strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")

    (repository / "upstream.txt").write_text("base\n")

    git("add", "upstream.txt")
    git("commit", "-m", "Initial upstream base")

    base = git("rev-parse", "HEAD")

    git("checkout", "-b", "custom")

    (repository / "custom.txt").write_text("custom patch\n")

    git("add", "custom.txt")
    git("commit", "-m", "Custom patch")

    custom = git("rev-parse", "HEAD")

    git("checkout", "main")

    (repository / "upstream.txt").write_text(
        "base\nupstream improvement\n"
    )

    git("add", "upstream.txt")
    git("commit", "-m", "New upstream release")

    latest = git("rev-parse", "HEAD")

    # Install a deliberately detectable hook.
    # Candidate preparation must not execute it.
    marker = root / "hook-executed"

    hook = repository / ".git/hooks/post-checkout"

    hook.write_text(
        "#!/bin/sh\n"
        f"touch '{marker}'\n"
    )

    hook.chmod(0o700)

    def invoke(args, overrides=None):
        execution_env = dict(env)
        execution_env.update(overrides or {})

        return subprocess.run(
            ["bash", str(helper), *args],
            cwd=repository,
            env=execution_env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    result = invoke([custom, base, latest])

    assert result.returncode == 0, result.stderr

    candidate = result.stdout.strip()

    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    assert candidate == git("rev-parse", "HEAD")
    assert candidate != custom

    git("merge-base", "--is-ancestor", latest, candidate)

    assert git("show", f"{candidate}:custom.txt") == (
        "custom patch"
    )

    assert git("show", f"{candidate}:upstream.txt") == (
        "base\nupstream improvement"
    )

    print("PASS: Exact candidate rebase.")
    print("PASS: New upstream ancestry verified.")
    print("PASS: Custom patch preserved.")
    print("PASS: Upstream improvement preserved.")

    assert not marker.exists()

    print("PASS: Checkout hook did not execute.")

    assert git("rev-parse", "refs/heads/custom") == custom

    print("PASS: Original custom branch unchanged.")

    for invalid in (
        "main",
        "--help",
        "v5.5.0",
        "invalid;command",
        "0" * 40,
    ):
        result = invoke([invalid, base, latest])

        assert result.returncode != 0, invalid
        assert not result.stdout.strip(), invalid

    print("PASS: Invalid and missing commit identities rejected.")

    result = invoke([custom, latest, base])

    assert result.returncode != 0
    assert not result.stdout.strip()

    print("PASS: Incorrect current-base ancestry rejected.")

    for variable in (
        "CUSTOM_BRANCH_TOKEN",
        "CUSTOM_GIT_TOKEN",
        "CUSTOM_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        result = invoke(
            [custom, base, latest],
            {variable: "synthetic-credential"},
        )

        assert result.returncode != 0, variable
        assert not result.stdout.strip(), variable
        assert "Unexpected credential" in result.stderr

    print("PASS: Unexpected credentials rejected.")

    (repository / "untracked.txt").write_text(
        "unexpected workspace content\n"
    )

    result = invoke([custom, base, latest])

    assert result.returncode != 0
    assert not result.stdout.strip()

    (repository / "untracked.txt").unlink()

    print("PASS: Dirty workspace rejected.")

print("PASS: All ten preparation boundary checks.")
print("No remote operations or real credentials used.")
