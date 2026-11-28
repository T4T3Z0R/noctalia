#!/usr/bin/env python3
# W01-C automation regression qualification.
# Uses disposable repositories and synthetic credentials.
# Qualification source: W01-C1.14a-integration-qualification.txt

from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile

scripts = Path(".github/scripts").resolve()

prepare = scripts / "prepare-candidate.sh"
transfer = scripts / "candidate-bundle.sh"

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)

    home = root / "home"
    home.mkdir(mode=0o700)

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }

    def execute(repository, command):
        return subprocess.run(
            command,
            cwd=repository,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def git(repository, *args):
        result = execute(repository, ["git", *args])

        assert result.returncode == 0, (
            repository,
            args,
            result.stderr,
        )

        return result.stdout.strip()

    def helper(repository, script, *args):
        return execute(
            repository,
            ["bash", str(script), *map(str, args)],
        )

    def initialize(name, bare=False):
        repository = root / name
        repository.mkdir(mode=0o700)

        options = ["init", "--template="]

        if bare:
            options.append("--bare")
        else:
            options.append("--initial-branch=workspace")

        git(repository, *options)

        if not bare:
            git(repository, "config", "user.name", "Synthetic Test")
            git(repository, "config", "user.email", "test@example.invalid")

        return repository

    def commit(repository, filename, contents, message):
        (repository / filename).write_text(contents)
        git(repository, "add", filename)
        git(repository, "commit", "-m", message)
        return git(repository, "rev-parse", "HEAD")

    upstream = initialize("upstream")

    base = commit(
        upstream,
        "upstream.txt",
        "original base\n",
        "Initial upstream base",
    )

    git(upstream, "tag", "v1.0.0")

    custom = initialize("custom")

    git(
        custom,
        "fetch",
        "--no-tags",
        str(upstream),
        "refs/tags/v1.0.0",
    )

    git(custom, "checkout", "-b", "custom", base)

    custom_sha = commit(
        custom,
        "custom.txt",
        "custom patch\n",
        "Custom modification",
    )

    latest = commit(
        upstream,
        "upstream.txt",
        "original base\nupstream improvement\n",
        "Updated upstream",
    )

    git(upstream, "tag", "v1.1.0")

    # Reproduce the workflow's empty-repository topology.
    preparation = initialize("preparation")

    git(
        preparation,
        "-c", "fetch.fsckObjects=true",
        "fetch", "--no-tags",
        str(custom),
        "refs/heads/custom",
    )

    assert git(
        preparation,
        "rev-parse",
        "--verify",
        "FETCH_HEAD^{commit}",
    ) == custom_sha

    git(
        preparation,
        "-c", "fetch.fsckObjects=true",
        "fetch", "--no-tags",
        str(upstream),
        "refs/tags/v1.1.0",
    )

    assert git(
        preparation,
        "rev-parse",
        "--verify",
        "FETCH_HEAD^{commit}",
    ) == latest

    assert git(
        preparation,
        "cat-file", "-t", base,
    ) == "commit"

    assert git(preparation, "status", "--porcelain") == ""

    assert execute(
        preparation,
        ["git", "rev-parse", "--verify", "HEAD"],
    ).returncode != 0

    print("PASS: Empty preparation repository seeded by fetch.")

    result = helper(
        preparation,
        prepare,
        custom_sha,
        base,
        latest,
    )

    assert result.returncode == 0, result.stderr

    candidate = result.stdout.strip()

    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    assert candidate != custom_sha

    git(
        preparation,
        "merge-base", "--is-ancestor",
        latest, candidate,
    )

    assert git(
        preparation,
        "show", f"{candidate}:custom.txt",
    ) == "custom patch"

    assert git(
        preparation,
        "show", f"{candidate}:upstream.txt",
    ) == "original base\nupstream improvement"

    print("PASS: Actual helper accepts workflow initialization topology.")
    print("PASS: Candidate source and upstream ancestry verified.")

    artifact = root / "candidate.bundle"

    result = helper(
        preparation,
        transfer,
        "export",
        artifact,
        candidate,
        latest,
        "-",
    )

    assert result.returncode == 0, result.stderr

    digest = result.stdout.strip()

    assert digest == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()

    print("PASS: Candidate bundle exported with exact checksum.")

    receiver = initialize("receiver", bare=True)

    git(
        receiver,
        "-c", "fetch.fsckObjects=true",
        "fetch", "--no-tags",
        str(upstream),
        "refs/tags/v1.1.0",
    )

    assert git(
        receiver,
        "rev-parse",
        "--verify",
        "FETCH_HEAD^{commit}",
    ) == latest

    result = helper(
        receiver,
        transfer,
        "import",
        artifact,
        candidate,
        latest,
        digest,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == candidate

    assert git(
        receiver,
        "rev-parse", "--is-bare-repository",
    ) == "true"

    assert not (receiver / "custom.txt").exists()

    print("PASS: Independent bare receiver imported exact candidate.")

    # Exercise publication mechanics locally.
    # No authentication helper or real credentials involved.
    publication = initialize("publication", bare=True)

    git(
        receiver,
        "remote", "add", "origin",
        str(publication),
    )

    git(
        receiver,
        "-c", "core.hooksPath=/dev/null",
        "push", "origin",
        "--force",
        f"{candidate}:refs/heads/automation/custom-candidate",
    )

    assert git(
        publication,
        "rev-parse",
        "refs/heads/automation/custom-candidate",
    ) == candidate

    assert git(
        publication,
        "show", f"{candidate}:custom.txt",
    ) == "custom patch"

    print("PASS: Exact candidate published to disposable local remote.")

    assert git(
        custom,
        "rev-parse", "refs/heads/custom",
    ) == custom_sha

    assert git(
        upstream,
        "rev-parse", "refs/tags/v1.1.0",
    ) == latest

    print("PASS: Both original source repositories unchanged.")

    # Test genuine conflicting changes to the same source line.
    git(custom, "checkout", "-b", "conflict", base)

    conflict_sha = commit(
        custom,
        "upstream.txt",
        "conflicting custom replacement\n",
        "Conflicting custom modification",
    )

    conflicting = initialize("conflicting-preparation")

    git(
        conflicting,
        "fetch", "--no-tags",
        str(custom),
        "refs/heads/conflict",
    )

    git(
        conflicting,
        "fetch", "--no-tags",
        str(upstream),
        "refs/tags/v1.1.0",
    )

    result = helper(
        conflicting,
        prepare,
        conflict_sha,
        base,
        latest,
    )

    assert result.returncode != 0, result.stderr
    assert result.stdout.strip() == ""
    assert "Candidate rebase failed." in result.stderr

    assert git(
        conflicting,
        "rev-parse", "HEAD",
    ) == conflict_sha

    assert git(
        conflicting,
        "status", "--porcelain",
    ) == ""

    assert not (
        conflicting / ".git/rebase-merge"
    ).exists()

    assert not (
        conflicting / ".git/rebase-apply"
    ).exists()

    print("PASS: Conflicting rebase rejected without candidate output.")
    print("PASS: Failed rebase aborted and working tree restored.")

print("PASS: All nine integrated Git checks.")
print("No network operations or actual credentials used.")
