#!/usr/bin/env python3
"""Regression coverage for custom workflow checkout credentials."""

from pathlib import Path
import copy
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


class WorkflowLoader(yaml.SafeLoader):
    """Avoid permissive YAML 1.1 boolean aliases."""

    def construct_mapping(self, node, deep=False):
        keys = set()

        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=False)

            if key in keys:
                raise ValueError(f"Duplicate YAML mapping key: {key}")

            keys.add(key)

        return super().construct_mapping(node, deep=deep)



WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)

for character, resolvers in list(
    WorkflowLoader.yaml_implicit_resolvers.items()
):
    WorkflowLoader.yaml_implicit_resolvers[character] = [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]

WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$"),
    list("tf"),
)


def load_workflow(source):
    return yaml.load(source, Loader=WorkflowLoader)


def checkout_steps(document):
    for job_name, job in document["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses", "")

            if uses.lower().startswith("actions/checkout@"):
                yield job_name, job, step


def checkout_policy(step):
    inputs = step.get("with")

    if not isinstance(inputs, dict):
        raise ValueError("Checkout inputs missing or invalid.")

    value = inputs.get("persist-credentials")

    if type(value) is not bool:
        raise ValueError(
            "Checkout persistence must be explicitly boolean."
        )

    return value


class CredentialPolicyTests(unittest.TestCase):

    def test_boolean_policy(self):
        for literal in (
            "no",
            "off",
            "0",
            "null",
            '"false"',
            "'false'",
            "${{ false }}",
        ):
            with self.subTest(literal=literal):
                step = load_workflow(
                    "with:\n"
                    f"  persist-credentials: {literal}\n"
                )

                with self.assertRaises(ValueError):
                    checkout_policy(step)

        for step in (
            {},
            {"with": {}},
            {"with": None},
            {"with": "invalid"},
        ):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    checkout_policy(step)

        self.assertIs(
            checkout_policy(load_workflow(
                "with:\n  persist-credentials: false\n"
            )),
            False,
        )

        self.assertIs(
            checkout_policy(load_workflow(
                "with:\n  persist-credentials: true\n"
            )),
            True,
        )


    def test_custom_checkout_inventory(self):
        exceptions = []
        files = sorted(
            list(WORKFLOWS.glob("*.yml"))
            + list(WORKFLOWS.glob("*.yaml"))
        )

        self.assertTrue(files)

        for path in files:
            document = load_workflow(path.read_text())

            for job_name, job, step in checkout_steps(document):
                identity = (path.name, job_name)

                with self.subTest(
                    workflow=path.name,
                    job=job_name,
                    step=step.get("name"),
                ):
                    if checkout_policy(step):
                        exceptions.append(identity)

                        self.assertIn(
                            identity,
                            {
                                ("cachix.yml", "push"),
                                ("update-flake.yml", "update"),
                            },
                        )

                        condition = job.get("if", "").strip()

                        if (
                            condition.startswith("${{")
                            and condition.endswith("}}")
                        ):
                            condition = condition[3:-2].strip()

                        self.assertEqual(
                            condition,
                            "github.repository == 'noctalia-dev/noctalia'",
                        )
                    else:
                        self.assertNotIn("token", step["with"])

        self.assertEqual(
            sorted(exceptions),
            [
                ("cachix.yml", "push"),
                ("update-flake.yml", "update"),
            ],
        )


POLICY_BRANCH = "custom"


UPSTREAM = "github.repository == 'noctalia-dev/noctalia'"

FORK_MAIN = (
    "github.repository == 'T4T3Z0R/noctalia' && "
    "github.ref == 'refs/heads/main'"
)

READ_ONLY = {"contents": "read"}

WRITERS = {
    ("cachix.yml", "push"): {"contents": "write"},
    ("close-v4-issues.yml", "close-v4-issue"): {
        "contents": "read",
        "issues": "write",
    },
    ("label-issue-metadata.yml", "label-issue-metadata"): {
        "contents": "read",
        "issues": "write",
    },
    ("release.yml", "release"): {"contents": "write"},
    ("update-flake.yml", "update"): {"contents": "write"},
}

GUARDS = {
    ("cachix.yml", "cache"): UPSTREAM,
    ("cachix.yml", "push"): UPSTREAM,
    ("close-v4-issues.yml", "close-v4-issue"):
        UPSTREAM + " && (github.event.issue.state == 'open')",
    ("label-issue-metadata.yml", "label-issue-metadata"):
        UPSTREAM,
    ("mirror-codeberg.yml", "mirror"): UPSTREAM,
    ("release.yml", "release"): UPSTREAM,
    ("update-flake.yml", "update"): UPSTREAM,
}

EXPLICIT_PERMISSIONS = {}

if POLICY_BRANCH == "control":
    WRITERS.update({
        ("update-custom.yml", "validate"): {
            "contents": "read",
            "actions": "write",
        },
        ("update-custom.yml", "cleanup"): {
            "contents": "write",
        },
    })

    GUARDS.update({
        ("update-aur-package.yml", "aur-sync"): UPSTREAM,
        ("update-custom.yml", "test-custom-token"):
            FORK_MAIN + " && (github.event_name == "
            "'workflow_dispatch' && inputs.test_custom_token)",
        ("update-custom.yml", "check"):
            FORK_MAIN + " && (github.event_name != "
            "'workflow_dispatch' || !inputs.test_custom_token)",
        ("update-custom.yml", "upstream-support"):
            FORK_MAIN
            + " && needs.check.outputs.changed == 'true'"
            + " && needs.check.outputs.upstream_support == 'true'"
            + " && needs.check.outputs.support_already_reported != 'true'",
        ("update-custom.yml", "rebase-candidate"):
            FORK_MAIN
            + " && needs.check.outputs.changed == 'true'"
            + " && needs.check.outputs.upstream_support != 'true'",
        ("update-custom.yml", "prepare"): FORK_MAIN,
        ("update-custom.yml", "validate"): FORK_MAIN,
        ("update-custom.yml", "promote"): FORK_MAIN,
        ("update-custom.yml", "cleanup"):
            FORK_MAIN + " && (always() && "
            "needs.prepare.result == 'success')",
        ("update-custom.yml", "notify"):
            "always() && " + FORK_MAIN + " && ("
            " (github.event_name != 'workflow_dispatch' || "
            "!inputs.test_custom_token) && ("
            " (github.event_name == 'workflow_dispatch' && "
            "inputs.test_pushover == true) ||"
            " needs.check.result == 'failure' ||"
            " ( needs.check.result == 'success' &&"
            " needs.check.outputs.changed == 'true' &&"
            " needs.check.outputs.upstream_support == 'true' &&"
            " needs.check.outputs.support_already_reported != 'true'"
            " ) ||"
            " needs.rebase-candidate.result == 'failure' ||"
            " needs.prepare.result == 'failure' ||"
            " needs.validate.result == 'failure' ||"
            " needs.promote.result == 'failure' ) )",
    })

    EXPLICIT_PERMISSIONS.update({
        ("update-custom.yml", name): READ_ONLY
        for name in (
            "test-custom-token",
            "check",
            "upstream-support",
            "rebase-candidate",
            "prepare",
            "promote",
        )
    })

    EXPLICIT_PERMISSIONS[
        ("update-custom.yml", "notify")
    ] = {}

elif POLICY_BRANCH == "custom":
    WRITERS.update({
        ("enforce-pr-template.yml", "enforce"): {
            "contents": "write",
            "issues": "write",
            "pull-requests": "write",
        },
        ("stale-needs-info.yml", "stale"): {
            "issues": "write",
        },
        ("triage-waiting.yml", "waiting"): {
            "issues": "write",
        },
    })

    GUARDS.update({
        ("enforce-pr-template.yml", "enforce"): UPSTREAM,
        ("project-intake.yml", "intake"): UPSTREAM,
        ("stale-needs-info.yml", "stale"): UPSTREAM,
        ("triage-waiting.yml", "waiting"):
            UPSTREAM + " && !github.event.issue.pull_request",
    })

else:
    raise ValueError("Unknown permission policy branch")


def canonical_guard(condition):
    if not isinstance(condition, str):
        return ""

    condition = condition.strip()

    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()

    # Preserve quoted literals exactly. Normalize only
    # whitespace outside literals and around parentheses.
    tokens = re.findall(
        r"'(?:[^']|'')*'|[^\s'()]+|[()]",
        condition,
    )

    return tuple(tokens)


def permission_violations(filename, document):
    violations = []

    defaults = document.get("permissions")

    if not isinstance(defaults, dict):
        return ["Missing explicit workflow permissions"]

    if any(value != "read" for value in defaults.values()):
        violations.append("Unexpected workflow-level authority")

    for name, job in document["jobs"].items():
        identity = (filename, name)

        effective = job.get("permissions", defaults)

        if not isinstance(effective, dict):
            violations.append(f"{name}: invalid permissions")
            continue

        if identity in WRITERS:
            if job.get("permissions") != WRITERS[identity]:
                violations.append(f"{name}: incorrect writer permissions")

        elif any(value != "read" for value in effective.values()):
            violations.append(f"{name}: unauthorized token permissions")

        if identity in EXPLICIT_PERMISSIONS:
            if job.get("permissions") != EXPLICIT_PERMISSIONS[identity]:
                violations.append(f"{name}: incorrect explicit permissions")

        if identity in GUARDS:
            if canonical_guard(job.get("if")) != canonical_guard(
                GUARDS[identity]
            ):
                violations.append(f"{name}: incorrect repository guard")

    return violations


class WorkflowPermissionTests(unittest.TestCase):
    def test_permission_inventory(self):
        writers = set()
        guards = set()
        explicit = set()

        paths = sorted(
            list(WORKFLOWS.glob("*.yml"))
            + list(WORKFLOWS.glob("*.yaml"))
        )

        self.assertTrue(paths)

        for path in paths:
            document = load_workflow(path.read_text())

            with self.subTest(workflow=path.name):
                self.assertEqual(
                    permission_violations(path.name, document),
                    [],
                )

                for name in document["jobs"]:
                    identity = (path.name, name)

                    if identity in WRITERS:
                        writers.add(identity)

                    if identity in GUARDS:
                        guards.add(identity)

                    if identity in EXPLICIT_PERMISSIONS:
                        explicit.add(identity)

        self.assertEqual(writers, set(WRITERS))
        self.assertEqual(guards, set(GUARDS))
        self.assertEqual(explicit, set(EXPLICIT_PERMISSIONS))

    def test_implicit_and_broad_permissions_rejected(self):
        for value in (
            None, "read-all", "write-all",
            {"contents": "write"},
        ):
            with self.subTest(value=value):
                self.assertTrue(permission_violations(
                    "example.yml",
                    {"permissions": value, "jobs": {}},
                ))

    def test_unapproved_writer_rejected(self):
        self.assertTrue(permission_violations(
            "example.yml",
            {
                "permissions": READ_ONLY,
                "jobs": {
                    "validate": {
                        "permissions": {"contents": "write"},
                    },
                },
            },
        ))

    def test_exact_writer_permissions_and_guard(self):
        document = {
            "permissions": {},
            "jobs": {
                "release": {
                    "permissions": {"contents": "write"},
                    "if": UPSTREAM,
                },
            },
        }

        self.assertEqual(
            permission_violations("release.yml", document),
            [],
        )

        for key, value in (
            ("permissions", None),
            ("permissions", {
                "contents": "write", "actions": "write",
            }),
            ("if", None),
            ("if", UPSTREAM + " || true"),
            ("if", "true"),
            ("if", FORK_MAIN),
        ):
            with self.subTest(key=key, value=value):
                modified = copy.deepcopy(document)
                modified["jobs"]["release"][key] = value

                self.assertTrue(permission_violations(
                    "release.yml", modified,
                ))

    def test_external_credential_guard(self):
        document = {
            "permissions": READ_ONLY,
            "jobs": {
                "mirror": {"if": UPSTREAM},
            },
        }

        self.assertEqual(
            permission_violations("mirror-codeberg.yml", document),
            [],
        )

        document["jobs"]["mirror"]["if"] = UPSTREAM + " || true"

        self.assertTrue(
            permission_violations("mirror-codeberg.yml", document)
        )

    def test_guard_normalization(self):
        self.assertEqual(
            canonical_guard(
                "${{ github.repository == 'noctalia-dev/noctalia' }}"
            ),
            canonical_guard(UPSTREAM),
        )

        self.assertNotEqual(
            canonical_guard(UPSTREAM + " || true"),
            canonical_guard(UPSTREAM),
        )

        self.assertNotEqual(
            canonical_guard(UPSTREAM.replace(
                "noctalia-dev/noctalia",
                "noctalia-dev/ noctalia",
            )),
            canonical_guard(UPSTREAM),
        )

    def test_duplicate_mapping_keys_rejected(self):
        with self.assertRaises(ValueError):
            load_workflow(
                "permissions:\n"
                "  contents: write\n"
                "  contents: read\n"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
