#!/usr/bin/env python3
"""Exercise the receiver using real disposable Git repositories."""

from pathlib import Path
import hashlib
import os
import subprocess
import tempfile

scripts = Path(".github/scripts").resolve()
receiver = scripts / "receive-candidate.sh"
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

    def execute(args, cwd=root, environment=None):
        return subprocess.run(
            list(map(str, args)),
            cwd=cwd,
            env=environment if environment is not None else env,
            text=True,
            capture_output=True,
            timeout=60,
        )

    def git(cwd, *args):
        result = execute(["git", *args], cwd)

        assert result.returncode == 0, result.stderr

        return result.stdout.strip()

    upstream = root / "upstream"
    upstream.mkdir()

    git(upstream, "init", "--template=", "--initial-branch=main")
    git(upstream, "config", "user.name", "Synthetic Test")
    git(upstream, "config", "user.email", "test@example.invalid")

    (upstream / "source.txt").write_text("upstream\n")

    git(upstream, "add", "source.txt")
    git(upstream, "commit", "-m", "Upstream release")

    base = git(upstream, "rev-parse", "HEAD")

    git(upstream, "tag", "v1.0.0")

    (upstream / "source.txt").write_text("candidate\n")

    git(upstream, "add", "source.txt")
    git(upstream, "commit", "-m", "Custom candidate")

    candidate = git(upstream, "rev-parse", "HEAD")

    bundle = root / "candidate.bundle"

    result = execute(
        [
            "bash", transfer, "export",
            bundle, candidate, base, "-",
        ],
        upstream,
    )

    assert result.returncode == 0, result.stderr

    digest = result.stdout.strip()

    assert digest == hashlib.sha256(
        bundle.read_bytes()
    ).hexdigest()

    # Substitute the network transport only. All Git object
    # import and verification operations remain real.
    bin_directory = root / "bin"
    bin_directory.mkdir()

    wrapper = bin_directory / "git"

    wrapper.write_text(
        """#!/usr/bin/env python3
import os
import sys

args = [
    os.environ["TEST_UPSTREAM"]
    if value == "https://github.com/noctalia-dev/noctalia.git"
    else value
    for value in sys.argv[1:]
]

os.execv("/usr/bin/git", ["git", *args])
"""
    )

    wrapper.chmod(0o700)

    receiver_env = dict(env)
    receiver_env["PATH"] = f"{bin_directory}:/usr/bin:/bin"
    receiver_env["TEST_UPSTREAM"] = str(upstream)

    def receive(
        destination,
        artifact=bundle,
        expected_candidate=candidate,
        expected_base=base,
        tag="v1.0.0",
        checksum=digest,
        overrides=None,
    ):
        environment = dict(receiver_env)
        environment.update(overrides or {})

        return execute(
            [
                "bash", receiver,
                destination, artifact,
                expected_candidate, expected_base,
                tag, checksum,
            ],
            environment=environment,
        )

    destination = root / "successful-receiver"

    result = receive(destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == candidate

    repository = destination / "repository"

    assert git(
        repository,
        "rev-parse",
        "--is-bare-repository",
    ) == "true"

    assert git(
        repository,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == candidate

    assert git(
        repository,
        "show",
        f"{candidate}:source.txt",
    ) == "candidate"

    assert not (repository / "source.txt").exists()

    print("PASS: Exact candidate reconstructed in bare repository.")

    second = root / "independent-receiver"

    result = receive(second)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == candidate

    print("PASS: Independent reconstruction returns identical revision.")

    result = receive(destination)

    assert result.returncode != 0

    assert git(
        repository,
        "rev-parse",
        "refs/heads/remediation-transfer",
    ) == candidate

    print("PASS: Existing receiver cannot be overwritten.")

    cases = [
        ("wrong-candidate", {"expected_candidate": "a" * 40}),
        ("wrong-base", {"expected_base": "b" * 40}),
        ("wrong-tag", {"tag": "v9.9.9"}),
        ("malformed-tag", {"tag": "refs/heads/main"}),
        ("wrong-digest", {"checksum": "0" * 64}),
        ("malformed-digest", {"checksum": "invalid"}),
        ("malformed-candidate", {"expected_candidate": "HEAD"}),
        ("malformed-base", {"expected_base": "HEAD"}),
    ]

    for name, arguments in cases:
        failed = root / name

        result = receive(failed, **arguments)

        assert result.returncode != 0, name
        assert not failed.exists(), name
        assert not result.stdout.strip(), name

    print(f"PASS: All {len(cases)} invalid identity cases rejected.")

    for variable in (
        "CUSTOM_BRANCH_TOKEN",
        "CUSTOM_GIT_TOKEN",
        "CUSTOM_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "NOCTALIA_CANDIDATE_APP_PRIVATE_KEY",
    ):
        failed = root / f"credential-{variable}"

        result = receive(
            failed,
            overrides={variable: "synthetic"},
        )

        assert result.returncode != 0, variable
        assert not failed.exists()
        assert not result.stdout.strip()

    print("PASS: All six unexpected credential variables rejected.")

    symlink = root / "linked.bundle"
    symlink.symlink_to(bundle)

    failed = root / "symlink-receiver"

    result = receive(failed, artifact=symlink)

    assert result.returncode != 0
    assert not failed.exists()

    print("PASS: Symbolic-link bundle rejected.")

    assert git(upstream, "rev-parse", "HEAD") == candidate
    assert git(upstream, "rev-parse", "refs/tags/v1.0.0") == base

    print("PASS: Original source repository unchanged.")

print("PASS: Trusted candidate receiver regression.")
print("No real credentials or network operations used.")
