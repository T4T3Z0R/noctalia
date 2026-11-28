#!/usr/bin/env python3
"""Native Docker qualification of the trusted validation driver.

Run only on a disposable CI runner.
No production credentials or remote Git mutations are required.
"""

from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]

DRIVER = ROOT / ".github/scripts/validate-candidate.sh"

PREFIX = "noctalia-validation"

FORBIDDEN_CREDENTIALS = (
    "CUSTOM_BRANCH_TOKEN",
    "CUSTOM_GIT_TOKEN",
    "CUSTOM_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NOCTALIA_CANDIDATE_APP_PRIVATE_KEY",
)

for variable in FORBIDDEN_CREDENTIALS:
    assert not os.environ.get(variable), (
        f"Unexpected qualification credential: {variable}"
    )


def execute(args, cwd=None, environment=None, timeout=1800):
    return subprocess.run(
        list(map(str, args)),
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def checked(args, cwd=None, environment=None):
    result = execute(args, cwd, environment)

    assert result.returncode == 0, (
        args, result.stdout, result.stderr
    )

    return result.stdout.strip()


def inventory():
    containers = checked([
        "docker", "container", "ls", "-a",
        "--format", "{{.Names}}",
    ])

    volumes = checked([
        "docker", "volume", "ls",
        "--format", "{{.Name}}",
    ])

    images = checked([
        "docker", "image", "ls",
        "--format", "{{.Repository}}:{{.Tag}}",
    ])

    return {
        "containers": {
            name for name in containers.splitlines()
            if name.startswith(PREFIX + "-")
        },
        "volumes": {
            name for name in volumes.splitlines()
            if name.startswith(PREFIX + "-")
        },
        "images": {
            name for name in images.splitlines()
            if name.startswith(PREFIX + ":")
        },
    }


def replay_untrusted_log(result):
    # Prefix every line so fixture output cannot be
    # interpreted as an Actions workflow command.
    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            print(f"[validation] {line}")


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)

    home = root / "home"
    home.mkdir(mode=0o700)

    environment = dict(os.environ)

    environment.update({
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",

        # Deliberate synthetic runner environment values.
        # None may appear inside candidate execution.
        "GITHUB_ENV": "/synthetic/runner/env",
        "GITHUB_OUTPUT": "/synthetic/runner/output",
        "GITHUB_PATH": "/synthetic/runner/path",
        "GITHUB_STEP_SUMMARY": "/synthetic/runner/summary",
        "ACTIONS_RUNTIME_TOKEN": "synthetic-runtime-token",
    })

    source = root / "source"
    source.mkdir(mode=0o700)

    def git(repository, *args):
        return checked(
            ["git", *args],
            repository,
            environment,
        )

    git(source, "init", "--template=", "--initial-branch=main")
    git(source, "config", "user.name", "Synthetic Test")
    git(source, "config", "user.email", "test@example.invalid")

    (source / "src").mkdir()
    (source / "tests").mkdir()

    (source / "src/main.cpp").write_text(
        "int main() { return 0; }\n"
    )

    (source / "tests/test.cpp").write_text(
        "int test() { return 0; }\n"
    )

    (source / "verify-isolation.sh").write_text(
        r'''#!/usr/bin/env bash
set -euo pipefail

test "$(id -u)" = 20000
test "$(id -g)" = 20000

test ! -S /var/run/docker.sock
test ! -S /run/docker.sock

test -r /input.bundle
test ! -w /input.bundle

test "$(awk '$1 == "CapEff:" {print $2}' /proc/self/status)" \
    = 0000000000000000

test "$(awk '$1 == "NoNewPrivs:" {print $2}' /proc/self/status)" \
    = 1

for variable in \
    GITHUB_ENV \
    GITHUB_OUTPUT \
    GITHUB_PATH \
    GITHUB_STEP_SUMMARY \
    ACTIONS_RUNTIME_TOKEN \
    GITHUB_TOKEN \
    GH_TOKEN \
    CUSTOM_GIT_TOKEN \
    CUSTOM_BRANCH_TOKEN \
    NOCTALIA_CANDIDATE_APP_PRIVATE_KEY
do
    test -z "${!variable:-}"
done

# Docker's none network exposes loopback only.
interfaces="$(ls -1 /sys/class/net)"
test "$interfaces" = lo

test ! -e /synthetic/runner/env
test ! -e /synthetic/runner/output

touch /source/writable-probe
rm /source/writable-probe

echo "ISOLATION_FIXTURE_PASSED"
'''
    )

    (source / "justfile").write_text(
        '''build:
    bash verify-isolation.sh
    c++ src/main.cpp -o fixture-program

test *args:
    ./fixture-program
    echo "TEST_FIXTURE_PASSED"

lint:
    echo "LINT_FIXTURE_PASSED"
'''
    )

    git(source, "add", ".")
    git(source, "commit", "-m", "Successful validation fixture")

    success_sha = git(source, "rev-parse", "HEAD")

    # Create a second candidate whose build recipe
    # deliberately fails after verifying isolation.
    (source / "justfile").write_text(
        '''build:
    bash verify-isolation.sh
    echo "INTENTIONAL_BUILD_FAILURE"
    exit 42

test *args:
    echo "UNEXPECTED_TEST_EXECUTION"

lint:
    echo "UNEXPECTED_LINT_EXECUTION"
'''
    )

    git(source, "add", "justfile")
    git(source, "commit", "-m", "Failing validation fixture")

    failure_sha = git(source, "rev-parse", "HEAD")

    def prepare_receiver(name, revision):
        receiver = root / name
        receiver.mkdir(mode=0o700)

        git(receiver, "init", "--bare", "--template=")

        git(
            receiver,
            "fetch", "--no-tags",
            str(source),
            revision,
        )

        git(
            receiver,
            "update-ref",
            "refs/heads/remediation-transfer",
            revision,
        )

        return receiver

    baseline = inventory()

    successful = prepare_receiver(
        "successful-receiver",
        success_sha,
    )

    result = execute(
        ["bash", DRIVER, successful, success_sha],
        ROOT,
        environment,
    )

    replay_untrusted_log(result)

    assert result.returncode == 0, result.returncode

    assert "ISOLATION_FIXTURE_PASSED" in result.stdout
    assert "TEST_FIXTURE_PASSED" in result.stdout
    assert "LINT_FIXTURE_PASSED" in result.stdout

    assert (
        f"PASS: Isolated validation completed for {success_sha}"
        in result.stdout
    )

    assert inventory() == baseline

    print("PASS: Native successful validation and isolation.")

    failing = prepare_receiver(
        "failing-receiver",
        failure_sha,
    )

    result = execute(
        ["bash", DRIVER, failing, failure_sha],
        ROOT,
        environment,
    )

    replay_untrusted_log(result)

    assert result.returncode != 0
    assert "ISOLATION_FIXTURE_PASSED" in result.stdout
    assert "INTENTIONAL_BUILD_FAILURE" in result.stdout

    assert "UNEXPECTED_TEST_EXECUTION" not in result.stdout
    assert "UNEXPECTED_LINT_EXECUTION" not in result.stdout

    assert "Candidate validation failed: status" in result.stderr

    assert inventory() == baseline

    print("PASS: Failed validation propagated and resources removed.")

    result = execute(
        ["bash", DRIVER, successful, failure_sha],
        ROOT,
        environment,
    )

    assert result.returncode != 0
    assert inventory() == baseline

    print("PASS: Incorrect candidate identity rejected.")

    for variable in FORBIDDEN_CREDENTIALS:
        contaminated = dict(environment)
        contaminated[variable] = "synthetic-forbidden-credential"

        result = execute(
            ["bash", DRIVER, successful, success_sha],
            ROOT,
            contaminated,
        )

        assert result.returncode != 0, variable

        assert (
            "Unexpected validation credential."
            in result.stderr
        ), variable

        assert inventory() == baseline

    print("PASS: All six unexpected credentials rejected.")

    assert git(
        successful,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == success_sha

    assert git(
        failing,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == failure_sha

    print("PASS: Both original receiver repositories unchanged.")

print("PASS: Native candidate validation qualification.")
