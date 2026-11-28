#!/usr/bin/env python3
"""Exercise actual updater shell code against disposable Git repositories."""

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
REAL_GIT = shutil.which("git")

assert REAL_GIT

workflow = yaml.load(
    (ROOT / ".github/workflows/update-custom.yml").read_text(),
    Loader=yaml.BaseLoader,
)


def workflow_step(job, name):
    matches = [
        step
        for step in workflow["jobs"][job]["steps"]
        if step.get("name") == name
    ]

    assert len(matches) == 1, (job, name)

    return matches[0]["run"]


promotion = workflow_step("promote", "Update custom branch")
discovery = workflow_step("check", "Compare releases")

for variable in (
    "CUSTOM_BRANCH_TOKEN",
    "CUSTOM_GIT_TOKEN",
    "CUSTOM_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NOCTALIA_CANDIDATE_APP_PRIVATE_KEY",
):
    assert not os.environ.get(variable), variable


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)

    home = root / "home"
    home.mkdir(mode=0o700)

    binaries = root / "bin"
    binaries.mkdir()

    seed = root / "seed"
    seed.mkdir()

    upstream = root / "upstream.git"
    remote = root / "fork.git"

    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
    }

    def execute(args, cwd=None, environment=None):
        return subprocess.run(
            list(map(str, args)),
            cwd=cwd,
            env=env if environment is None else environment,
            text=True,
            capture_output=True,
            timeout=120,
        )

    def checked(args, cwd=None, environment=None):
        result = execute(args, cwd, environment)

        assert result.returncode == 0, (
            args,
            result.stdout,
            result.stderr,
        )

        return result.stdout.strip()

    def git(repository, *args):
        return checked([REAL_GIT, *args], repository)

    git(seed, "init", "--template=", "--initial-branch=main")
    git(seed, "config", "user.name", "Synthetic Test")
    git(seed, "config", "user.email", "test@example.invalid")

    def commit(message):
        git(seed, "commit", "--allow-empty", "-m", message)
        return git(seed, "rev-parse", "HEAD")

    base = commit("Synthetic upstream v1.0.0")
    git(seed, "tag", "v1.0.0", base)

    latest = commit("Synthetic upstream v2.0.0")
    git(seed, "tag", "v2.0.0", latest)

    git(seed, "checkout", "--detach", base)
    old = commit("Existing approved custom source")
    other = commit("Concurrent production revision")

    git(seed, "checkout", "--detach", latest)
    candidate = commit("Validated replacement candidate")

    for repository in (upstream, remote):
        repository.mkdir()
        git(repository, "init", "--bare", "--template=")

        for revision in (base, latest, old, other, candidate):
            git(repository, "fetch", "--no-tags", str(seed), revision)

    git(upstream, "update-ref", "refs/tags/v1.0.0", base)
    git(upstream, "update-ref", "refs/tags/v2.0.0", latest)

    # The fork deliberately has conflicting release tags.
    git(remote, "update-ref", "refs/tags/v1.0.0", other)
    git(remote, "update-ref", "refs/tags/v9.9.9", old)

    production_ref = "refs/heads/custom"
    archive_ref = f"refs/heads/archive/custom-{old}"

    # The wrapper preserves the real configured origin URL.
    # Only actual transport operations are redirected.
    wrapper = binaries / "git"

    wrapper.write_text(
        f"#!{sys.executable}\n"
        + r'''import os
import subprocess
import sys

args = sys.argv[1:]
real = os.environ["FIXTURE_REAL_GIT"]

if args and args[0] == "push":
    race = os.environ.get("FIXTURE_RACE", "")
    refspec = args[2] if len(args) > 2 else ""

    if race == "archive" and ":refs/heads/archive/" in refspec:
        ref = refspec.split(":", 1)[1]

        subprocess.run(
            [
                real, "-C", os.environ["FIXTURE_REMOTE"],
                "update-ref", ref, os.environ["FIXTURE_OTHER"],
            ],
            check=True,
        )

    if race == "production" and refspec.endswith(":refs/heads/custom"):
        subprocess.run(
            [
                real, "-C", os.environ["FIXTURE_REMOTE"],
                "update-ref", "refs/heads/custom",
                os.environ["FIXTURE_OTHER"],
            ],
            check=True,
        )

# Never rewrite remote inspection commands. The production
# authentication helper must still see the exact official URL.
if any(arg in ("fetch", "push", "ls-remote") for arg in args):
    args = [
        "-c",
        "url.file://" + os.environ["FIXTURE_REMOTE"]
        + ".insteadOf=https://github.com/T4T3Z0R/noctalia.git",
        "-c",
        "url.file://" + os.environ["FIXTURE_UPSTREAM"]
        + ".insteadOf=https://github.com/noctalia-dev/noctalia.git",
        *args,
    ]

os.execv(real, [real, *args])
'''
    )

    wrapper.chmod(0o755)

    gh = binaries / "gh"

    gh.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'test "$1" = api\n'
        'test "$2" = repos/noctalia-dev/noctalia/releases/latest\n'
        'printf "%s\\n" v2.0.0\n'
    )

    gh.chmod(0o755)

    fixture_env = dict(env)

    fixture_env.update({
        "PATH": f"{binaries}:{env['PATH']}",
        "FIXTURE_REAL_GIT": REAL_GIT,
        "FIXTURE_REMOTE": str(remote),
        "FIXTURE_UPSTREAM": str(upstream),
        "FIXTURE_OTHER": other,
        "GITHUB_REPOSITORY": "T4T3Z0R/noctalia",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": git(ROOT, "rev-parse", "HEAD"),
        "GITHUB_WORKSPACE": str(ROOT),
        "UPSTREAM_REPO": "noctalia-dev/noctalia",
        "CUSTOM_BRANCH": "custom",
        "TABLET_MARKER_BRANCH": "automation/upstream-tablet-detected",
    })

    def read_ref(ref):
        result = execute(
            [REAL_GIT, "show-ref", "--verify", "--hash", ref],
            remote,
        )

        if result.returncode == 0:
            return result.stdout.strip()

        return None

    def reset_remote():
        git(remote, "update-ref", production_ref, old)
        git(remote, "update-ref", "-d", archive_ref)

    def run_discovery(label):
        scratch = root / label
        scratch.mkdir()

        output = scratch / "outputs"
        summary = scratch / "summary"

        output.touch()
        summary.touch()

        environment = dict(fixture_env)

        environment.update({
            "RUNNER_TEMP": str(scratch),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        })

        result = execute(
            ["bash", "-c", discovery],
            ROOT,
            environment,
        )

        values = dict(
            line.split("=", 1)
            for line in output.read_text().splitlines()
            if "=" in line
        )

        return result, values

    reset_remote()

    result, values = run_discovery("discovery-valid")

    assert result.returncode == 0, (
        result.stdout,
        result.stderr,
    )

    assert values["current_tag"] == "v1.0.0"
    assert values["current_base_sha"] == base
    assert values["latest_tag"] == "v2.0.0"
    assert values["latest_sha"] == latest
    assert values["custom_sha"] == old
    assert values["changed"] == "true"
    assert values["upstream_support"] == "false"

    print("PASS: Conflicting fork tags cannot contaminate discovery.")

    git(upstream, "update-ref", "refs/tags/v2.0.1", latest)

    result, _ = run_discovery("discovery-alias")

    assert result.returncode != 0

    git(upstream, "update-ref", "-d", "refs/tags/v2.0.1")

    print("PASS: Ambiguous stable upstream aliases rejected.")

    # Preserve the candidate's actual upstream base
    # under a stable release tag.
    git(upstream, "update-ref", "refs/tags/v1.1.0", latest)

    # Present a higher-version release on divergent history.
    git(upstream, "update-ref", "refs/tags/v2.0.0", old)
    git(remote, "update-ref", production_ref, candidate)

    result, _ = run_discovery("discovery-divergent")

    assert result.returncode != 0

    git(upstream, "update-ref", "refs/tags/v2.0.0", latest)

    git(upstream, "update-ref", "-d", "refs/tags/v1.1.0")

    print("PASS: Divergent upstream release selection rejected.")

    # Ancestry alone is insufficient: a release with
    # an older version number can still be a descendant.
    reset_remote()

    git(upstream, "update-ref", "-d", "refs/tags/v1.0.0")
    git(upstream, "update-ref", "refs/tags/v3.0.0", base)

    result, _ = run_discovery("discovery-version-regression")

    assert result.returncode != 0, (
        result.stdout,
        result.stderr,
    )

    assert "Upstream release version regressed." in result.stderr

    git(upstream, "update-ref", "-d", "refs/tags/v3.0.0")
    git(upstream, "update-ref", "refs/tags/v1.0.0", base)

    print("PASS: Numerically backward release rejected despite valid ancestry.")

    def run_promotion(label, race=""):
        scratch = root / label
        scratch.mkdir()

        destination = scratch / "promotion-receiver"
        destination.mkdir()

        receiver_home = destination / "home"
        receiver_home.mkdir()

        repository = destination / "repository"
        repository.mkdir()

        git(repository, "init", "--bare", "--template=")

        git(
            repository,
            "fetch", "--no-tags",
            str(seed), candidate,
        )

        git(
            repository,
            "update-ref",
            "refs/heads/remediation-transfer",
            candidate,
        )

        summary = scratch / "summary"
        summary.touch()

        environment = dict(fixture_env)

        environment.update({
            "RUNNER_TEMP": str(scratch),
            "GITHUB_STEP_SUMMARY": str(summary),
            "CUSTOM_GIT_TOKEN": "synthetic-noncredential",
            "CUSTOM_GIT_ROLE": "promotion",
            "EXPECTED_CUSTOM_SHA": old,
            "CANDIDATE_SHA": candidate,
            "FIXTURE_RACE": race,
        })

        return execute(
            ["bash", "-c", promotion],
            ROOT,
            environment,
        )

    reset_remote()

    result = run_promotion("promotion-success")

    assert result.returncode == 0, (
        result.stdout,
        result.stderr,
    )

    assert read_ref(production_ref) == candidate
    assert read_ref(archive_ref) == old

    print("PASS: Real Git promotion retains displaced source.")

    reset_remote()

    git(remote, "update-ref", archive_ref, old)

    result = run_promotion("promotion-reuse")

    assert result.returncode == 0, (
        result.stdout,
        result.stderr,
    )

    assert read_ref(production_ref) == candidate
    assert read_ref(archive_ref) == old

    print("PASS: Existing exact rollback archive reused.")

    reset_remote()

    git(remote, "update-ref", archive_ref, other)

    result = run_promotion("promotion-conflict")

    assert result.returncode != 0
    assert read_ref(production_ref) == old
    assert read_ref(archive_ref) == other

    print("PASS: Conflicting archive blocks production mutation.")

    reset_remote()

    git(remote, "update-ref", production_ref, other)

    result = run_promotion("promotion-stale")

    assert result.returncode != 0
    assert read_ref(production_ref) == other
    assert read_ref(archive_ref) is None

    print("PASS: Stale production observation rejected before retention.")

    reset_remote()

    result = run_promotion(
        "promotion-archive-race",
        race="archive",
    )

    assert result.returncode != 0
    assert read_ref(production_ref) == old
    assert read_ref(archive_ref) == other

    print("PASS: Real create-only lease rejects concurrent archive creation.")

    reset_remote()

    result = run_promotion(
        "promotion-production-race",
        race="production",
    )

    assert result.returncode != 0
    assert read_ref(production_ref) == other
    assert read_ref(archive_ref) == old

    print("PASS: Real production lease rejects concurrent mutation.")
    print("PASS: Verified rollback archive survives failed promotion.")

print("PASS: Native Git discovery and transaction qualification.")
print("No network transport or real credentials used.")
