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


if __name__ == "__main__":
    unittest.main(verbosity=2)
