#!/usr/bin/env python3
# W01-C automation regression qualification.
# Uses disposable repositories and synthetic credentials.
# Qualification source: W01-C1.13c-authentication-qualification.txt

from pathlib import Path
import base64
import json
import os
import subprocess
import tempfile

helper = Path(
    ".github/scripts/authenticated-git.sh"
).resolve()

token = "synthetic-test-credential"

authorization = base64.b64encode(
    f"x-access-token:{token}".encode()
).decode()

with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)

    fake_git = root / "git"

    fake_git.write_text(
        r'''#!/usr/bin/env python3
import json
import os
import sys

assert "CUSTOM_GIT_TOKEN" not in os.environ

args = sys.argv[1:]

if args == ["remote", "get-url", "--push", "--all", "origin"]:
    assert "GIT_CONFIG_COUNT" not in os.environ
    print(os.environ["TEST_DESTINATION"])
    sys.exit(0)

with open(os.environ["TEST_RECEIPT"], "w") as output:
    json.dump({
        "args": args,
        "environment": {
            key: value
            for key, value in os.environ.items()
            if key.startswith("GIT_CONFIG_")
            or key == "GIT_TERMINAL_PROMPT"
        },
    }, output)

sys.exit(int(os.environ.get("TEST_GIT_STATUS", "0")))
'''
    )

    fake_git.chmod(0o700)

    receipt = root / "receipt.json"

    base_env = {
        "PATH": f"{root}:/usr/bin:/bin",
        "HOME": scratch,
        "LANG": "C",
        "CUSTOM_GIT_TOKEN": token,
        "CUSTOM_GIT_ROLE": "marker",
        "GITHUB_REPOSITORY": "T4T3Z0R/noctalia",
        "GITHUB_REF": "refs/heads/main",
        "TEST_DESTINATION":
            "https://github.com/T4T3Z0R/noctalia.git",
        "TEST_RECEIPT": str(receipt),
    }

    def invoke(command, overrides=None):
        receipt.unlink(missing_ok=True)

        env = dict(base_env)
        env.update(overrides or {})

        return subprocess.run(
            [
                "bash", "-c",
                'set -euo pipefail\n'
                'source "$1"\n'
                'shift\n'
                + command,
                "test",
                str(helper),
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    candidate_ref = "refs/heads/automation/custom-candidate"
    marker_ref = "refs/heads/automation/upstream-tablet-detected"
    promotion_ref = "refs/heads/custom"

    revision = "a" * 40
    previous = "b" * 40

    def push_command(ref, expected="", source=revision):
        return (
            f"authenticated_git push origin {source}:{ref} "
            f"--force-with-lease={ref}:{expected}"
        )

    command = push_command(marker_ref)

    result = invoke(command)

    assert result.returncode == 0, result.stderr
    assert receipt.exists()

    data = json.loads(receipt.read_text())

    assert data["args"] == [
        "push",
        "origin",
        f"{revision}:{marker_ref}",
        f"--force-with-lease={marker_ref}:",
    ]

    configuration = data["environment"]

    assert configuration["GIT_CONFIG_COUNT"] == "5"
    assert configuration["GIT_CONFIG_VALUE_0"] == ""
    assert configuration["GIT_CONFIG_VALUE_1"] == (
        f"AUTHORIZATION: basic {authorization}"
    )
    assert configuration["GIT_CONFIG_VALUE_2"] == ""
    assert configuration["GIT_CONFIG_VALUE_3"] == "/dev/null"
    assert configuration["GIT_CONFIG_VALUE_4"] == "false"
    assert configuration["GIT_TERMINAL_PROMPT"] == "0"

    assert result.stdout == (
        f"::add-mask::{authorization}\n"
    )

    print("PASS: Authorized push configuration.")

    result = invoke(
        command
        + "\n"
        + "test -z \"${GIT_CONFIG_COUNT+x}\"\n"
        + "test -z \"$(env | grep '^CUSTOM_GIT_TOKEN=' || true)\""
    )

    assert result.returncode == 0, result.stderr

    print("PASS: Authentication configuration does not persist.")
    print("PASS: Raw token is not exported to child processes.")

    result = invoke(command, {
        "CUSTOM_GIT_TOKEN": "",
    })

    assert result.returncode != 0
    assert not receipt.exists()

    print("PASS: Missing credential rejected.")

    result = invoke(command, {
        "GITHUB_REPOSITORY": "untrusted/noctalia",
    })

    assert result.returncode != 0
    assert not receipt.exists()

    print("PASS: Incorrect repository rejected.")

    for invalid_ref in (
        "refs/heads/custom",
        "refs/heads/automation/custom-candidate",
        "",
    ):
        result = invoke(command, {
            "GITHUB_REF": invalid_ref,
        })

        assert result.returncode != 0
        assert not receipt.exists()
        assert "Unexpected control execution ref" in result.stderr

    print("PASS: Non-main authentication rejected.")


    for destination in (
        "https://example.invalid/noctalia.git",
        "https://github.com/other/noctalia.git",
        "git@github.com:T4T3Z0R/noctalia.git",
        "https://github.com/T4T3Z0R/noctalia.git\n"
        "https://example.invalid/second.git",
    ):
        result = invoke(command, {
            "TEST_DESTINATION": destination,
        })

        assert result.returncode != 0
        assert not receipt.exists()

    print("PASS: Incorrect and multiple push destinations rejected.")

    for unsupported in (
        "authenticated_git fetch origin",
        "authenticated_git push upstream HEAD",
        "authenticated_git status",
    ):
        result = invoke(unsupported)

        assert result.returncode != 0
        assert not receipt.exists()

    print("PASS: Unsupported authentication operations rejected.")

    for role, ref, expected in (
        ("marker", marker_ref, ""),
        ("marker", marker_ref, previous),
        ("promotion", promotion_ref, previous),
    ):
        result = invoke(
            push_command(ref, expected),
            {"CUSTOM_GIT_ROLE": role},
        )

        assert result.returncode == 0, (
            role, result.stderr
        )

        assert receipt.exists()

    print("PASS: Three authorized mutation operations.")

    archive_ref = f"refs/heads/archive/custom-{revision}"

    result = invoke(
        push_command(archive_ref),
        {"CUSTOM_GIT_ROLE": "retention"},
    )

    assert result.returncode == 0, result.stderr
    assert receipt.exists()

    print("PASS: Exact create-only retention operation authorized.")

    denied = [

        ("retention", push_command(archive_ref, previous)),
        ("retention", push_command(archive_ref, source="")),
        ("retention", push_command(archive_ref, previous, source="")),
        ("retention", push_command(archive_ref, source=previous)),
        ("retention", push_command("refs/heads/archive/custom-invalid")),
        ("retention", push_command("refs/heads/archive/arbitrary")),
        ("retention", push_command(marker_ref)),
        ("retention", push_command(candidate_ref)),
        ("retention", push_command(promotion_ref, previous)),
        ("retention", push_command("refs/heads/main", previous)),
        ("retention", push_command("refs/tags/v1.2.3")),
        ("marker", push_command(archive_ref)),
        ("promotion", push_command(archive_ref)),
        ("", command),
        ("unknown", command),
        ("candidate", push_command(candidate_ref)),
        ("candidate", push_command(candidate_ref, previous)),
        ("candidate", push_command(candidate_ref, previous, source="")),
        ("candidate", push_command(marker_ref)),
        ("candidate", push_command(promotion_ref, previous)),
        ("marker", push_command(candidate_ref)),
        ("marker", push_command(candidate_ref, previous, source="")),
        ("marker", push_command(promotion_ref, previous)),
        ("marker", push_command(marker_ref, source="HEAD")),
        ("marker", push_command(marker_ref, "invalid")),
        ("marker", push_command(marker_ref, source="")),
        ("marker", push_command(marker_ref, previous, source="")),
        ("marker", push_command("refs/heads/arbitrary")),
        ("marker", push_command("refs/heads/main", previous)),
        ("marker", push_command("refs/tags/v1.2.3")),
        ("marker", push_command(marker_ref, source="*")),
        ("marker", command + " --force"),
        (
            "marker",
            f"authenticated_git push origin "
            f"{revision}:{marker_ref} --force",
        ),
        (
            "marker",
            f"authenticated_git push origin "
            f"{revision}:{marker_ref}",
        ),
        (
            "marker",
            f"authenticated_git push origin "
            f"{revision}:{marker_ref} "
            f"--force-with-lease={promotion_ref}:",
        ),
        (
            "marker",
            f"authenticated_git push origin "
            f"--delete {marker_ref}",
        ),
        ("promotion", push_command(promotion_ref)),
        ("promotion", push_command(promotion_ref, previous, source="")),
        ("promotion", push_command(candidate_ref)),
        ("promotion", push_command(marker_ref, previous)),
        ("promotion", push_command("refs/heads/main", previous)),
        ("promotion", push_command("refs/tags/v1.2.3", previous)),
    ]

    for role, mutation in denied:
        result = invoke(
            mutation,
            {"CUSTOM_GIT_ROLE": role},
        )

        assert result.returncode != 0, (
            role, mutation, result.stdout
        )

        assert not receipt.exists(), (
            role, mutation
        )

    print(f"PASS: All {len(denied)} unauthorized mutations rejected.")

    result = invoke(command, {
        "TEST_GIT_STATUS": "17",
    })

    assert result.returncode == 17

    print("PASS: Git failure status preserved.")

print("PASS: Authentication and role boundary qualification.")
print("No real remote operations or credentials used.")
