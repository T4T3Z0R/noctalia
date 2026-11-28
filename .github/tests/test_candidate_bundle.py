#!/usr/bin/env python3
# W01-C automation regression qualification.
# Uses disposable repositories and synthetic credentials.
# Qualification source: W01-C1.13f.2-bundle-qualification.txt

from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile

helper = Path(
    ".github/scripts/candidate-bundle.sh"
).resolve()

with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)

    source = root / "source"
    source.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": scratch,
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }

    def git(repository, *args):
        return subprocess.check_output(
            ["git", *args],
            cwd=repository,
            env=env,
            text=True,
            stderr=subprocess.PIPE,
            timeout=60,
        ).strip()

    def invoke(repository, *args, overrides=None):
        execution_env = dict(env)
        execution_env.update(overrides or {})

        return subprocess.run(
            ["bash", str(helper), *map(str, args)],
            cwd=repository,
            env=execution_env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    git(source, "init", "--initial-branch=main")
    git(source, "config", "user.name", "Synthetic Test")
    git(source, "config", "user.email", "test@example.invalid")

    (source / "upstream.txt").write_text("upstream base\n")

    git(source, "add", "upstream.txt")
    git(source, "commit", "-m", "Upstream base")

    base = git(source, "rev-parse", "HEAD")

    # Fresh bare receivers initially contain upstream
    # history, but not candidate objects.
    def receiver(name, seed=True):
        repository = root / name
        repository.mkdir()

        git(repository, "init", "--bare")

        if seed:
            git(
                repository,
                "fetch",
                "--no-tags",
                str(source),
                f"{base}:refs/heads/upstream",
            )

        return repository

    destination = receiver("destination")

    (source / "candidate.txt").write_text("custom source\n")

    git(source, "add", "candidate.txt")
    git(source, "commit", "-m", "Candidate commit")

    candidate = git(source, "rev-parse", "HEAD")

    bundle = root / "candidate.bundle"

    result = invoke(
        source,
        "export",
        bundle,
        candidate,
        base,
        "-",
    )

    assert result.returncode == 0, result.stderr

    digest = result.stdout.strip()

    assert re.fullmatch(r"[0-9a-f]{64}", digest)

    assert digest == hashlib.sha256(
        bundle.read_bytes()
    ).hexdigest()

    print("PASS: Exact candidate bundle export.")
    print("PASS: Bundle checksum verified independently.")

    assert git(
        source,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/remediation-transfer",
    ) == ""

    print("PASS: Temporary export reference removed.")

    result = invoke(
        destination,
        "import",
        bundle,
        candidate,
        base,
        digest,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == candidate

    assert git(
        destination,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == candidate

    assert git(
        destination,
        "show",
        f"{candidate}:candidate.txt",
    ) == "custom source"

    git(destination, "merge-base", "--is-ancestor", base, candidate)

    print("PASS: Exact candidate imported into bare repository.")
    print("PASS: Candidate content and ancestry preserved.")

    assert git(
        destination,
        "rev-parse",
        "--is-bare-repository",
    ) == "true"

    assert not (destination / "candidate.txt").exists()

    print("PASS: No candidate working tree materialized.")

    result = invoke(
        destination,
        "import",
        bundle,
        candidate,
        base,
        digest,
    )

    assert result.returncode != 0
    assert not result.stdout.strip()

    assert git(
        destination,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == candidate

    print("PASS: Existing transfer reference cannot be overwritten.")

    clean = receiver("negative-tests")

    def rejected(*args, repository=clean, overrides=None):
        result = invoke(
            repository,
            *args,
            overrides=overrides,
        )

        assert result.returncode != 0, result.stderr
        assert not result.stdout.strip()

        assert git(
            repository,
            "for-each-ref",
            "--format=%(refname)",
            "refs/heads/remediation-transfer",
        ) == ""

    rejected(
        "import",
        bundle,
        candidate,
        base,
        "0" * 64,
    )

    print("PASS: Incorrect bundle checksum rejected.")

    rejected(
        "import",
        bundle,
        base,
        base,
        digest,
    )

    print("PASS: Incorrect expected candidate rejected.")

    missing_base = receiver("missing-base", seed=False)

    rejected(
        "import",
        bundle,
        candidate,
        base,
        digest,
        repository=missing_base,
    )

    print("PASS: Missing prerequisite history rejected.")

    for variable in (
        "CUSTOM_BRANCH_TOKEN",
        "CUSTOM_GIT_TOKEN",
        "CUSTOM_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    ):
        rejected(
            "import",
            bundle,
            candidate,
            base,
            digest,
            overrides={variable: "synthetic-credential"},
        )

    print("PASS: Unexpected transfer credentials rejected.")

    symlink = root / "symlink.bundle"
    symlink.symlink_to(bundle)

    rejected(
        "import",
        symlink,
        candidate,
        base,
        digest,
    )

    print("PASS: Symbolic-link bundle rejected.")

    tampered = root / "tampered.bundle"
    tampered.write_bytes(bundle.read_bytes() + b"tampered")

    rejected(
        "import",
        tampered,
        candidate,
        base,
        digest,
    )

    print("PASS: Modified artifact rejected.")

    result = invoke(
        source,
        "export",
        bundle,
        candidate,
        base,
        "-",
    )

    assert result.returncode != 0

    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == digest

    print("PASS: Existing bundle cannot be overwritten.")

    # A rebase may produce no additional custom commits.
    # Exercise the equal-base special case explicitly.
    empty_patch_bundle = root / "equal-base.bundle"
    equal_receiver = receiver("equal-base")

    result = invoke(
        source,
        "export",
        empty_patch_bundle,
        base,
        base,
        "-",
    )

    assert result.returncode == 0, result.stderr

    equal_digest = result.stdout.strip()

    result = invoke(
        equal_receiver,
        "import",
        empty_patch_bundle,
        base,
        base,
        equal_digest,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == base

    print("PASS: Candidate identical to upstream base supported.")

print("PASS: All fifteen reported transfer boundary checks.")
print("No remote services or real credentials used.")
