#!/usr/bin/env python3
"""Regression coverage for trusted control workflow credentials."""

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


def find_step(job, name):
    matches = [
        step for step in job["steps"]
        if step.get("name") == name
    ]

    if len(matches) != 1:
        raise ValueError(f"Expected exactly one step: {name}")

    return matches[0]


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

    def test_control_checkout_inventory(self):
        exceptions = []
        files = sorted(
            list(WORKFLOWS.glob("*.yml"))
            + list(WORKFLOWS.glob("*.yaml"))
        )

        self.assertTrue(files)

        for path in files:
            document = load_workflow(path.read_text())

            for job_name, job, step in checkout_steps(document):
                with self.subTest(
                    workflow=path.name,
                    job=job_name,
                    step=step.get("name"),
                ):
                    if checkout_policy(step):
                        exceptions.append(path.name)

                        self.assertIn(
                            path.name,
                            {"cachix.yml", "update-flake.yml"},
                        )

                        condition = job.get("if", "")

                        self.assertRegex(
                            condition,
                            r"github\.repository\s*==\s*"
                            r"'noctalia-dev/noctalia'",
                        )
                    else:
                        self.assertNotIn(
                            "token",
                            step["with"],
                        )

        self.assertEqual(
            sorted(exceptions),
            ["cachix.yml", "update-flake.yml"],
        )

    def test_updater_checkouts(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        expected_jobs = {
            "test-custom-token",
            "check",
            "upstream-support",
            "rebase-candidate",
            "prepare",
            "promote",
            "cleanup",
        }

        actual_jobs = []

        for name, job, step in checkout_steps(workflow):
            with self.subTest(job=name):
                actual_jobs.append(name)

                self.assertIs(checkout_policy(step), False)

                self.assertEqual(
                    step["with"]["ref"],
                    "${{ github.sha }}",
                )

                self.assertNotIn("token", step["with"])

                condition = job.get("if", "")

                self.assertIn(
                    "github.repository == 'T4T3Z0R/noctalia'",
                    condition,
                )

                self.assertIn(
                    "github.ref == 'refs/heads/main'",
                    condition,
                )

        self.assertEqual(set(actual_jobs), expected_jobs)
        self.assertEqual(len(actual_jobs), len(expected_jobs))

    def test_candidate_job_separation(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        jobs = workflow["jobs"]

        rebase = jobs["rebase-candidate"]
        prepare = jobs["prepare"]

        self.assertEqual(rebase["needs"], "check")
        self.assertEqual(
            prepare["needs"],
            ["check", "rebase-candidate"],
        )

        self.assertEqual(
            rebase["permissions"],
            {"contents": "read"},
        )

        self.assertEqual(
            prepare["permissions"],
            {"contents": "read"},
        )

        self.assertNotIn("secrets.", str(rebase))
        self.assertNotIn("CUSTOM_GIT_TOKEN", str(rebase))
        self.assertNotIn("authenticated_git", str(rebase))

        self.assertEqual(
            prepare["outputs"]["candidate_sha"],
            "${{ steps.publish.outputs.candidate_sha }}",
        )

        publish = find_step(
            prepare,
            "Publish exact candidate reference",
        )

        receive = find_step(
            prepare,
            "Verify and import candidate objects",
        )

        self.assertNotIn("secrets.", str(receive))
        self.assertNotIn("CUSTOM_GIT_TOKEN", str(receive))

        self.assertLess(
            prepare["steps"].index(receive),
            prepare["steps"].index(publish),
        )

        self.assertEqual(
            publish["env"]["CUSTOM_GIT_TOKEN"],
            "${{ secrets.CUSTOM_BRANCH_TOKEN }}",
        )

        self.assertIn("git init --bare", receive["run"])
        self.assertIn("candidate-bundle.sh", receive["run"])
        self.assertIn("authenticated_git push", publish["run"])

        self.assertNotIn("git checkout", receive["run"])
        self.assertNotIn("git checkout", publish["run"])

    def test_artifact_identity_binding(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        rebase = workflow["jobs"]["rebase-candidate"]
        prepare = workflow["jobs"]["prepare"]

        upload = find_step(
            rebase,
            "Upload exact candidate bundle",
        )

        download = find_step(
            prepare,
            "Download exact candidate bundle",
        )

        self.assertEqual(upload["id"], "upload")

        self.assertEqual(
            rebase["outputs"]["artifact_id"],
            "${{ steps.upload.outputs.artifact-id }}",
        )

        self.assertEqual(
            download["with"]["artifact-ids"],
            "${{ needs.rebase-candidate.outputs.artifact_id }}",
        )

        self.assertEqual(
            upload["with"]["path"],
            "${{ runner.temp }}/candidate-transfer/candidate.bundle",
        )

        self.assertEqual(
            upload["with"]["if-no-files-found"],
            "error",
        )

    def test_authenticated_consumers(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        expected = {
            "test-custom-token":
                "Verify temporary branch creation and deletion",
            "upstream-support":
                "Record exact upstream marker",
            "prepare":
                "Publish exact candidate reference",
            "promote":
                "Update custom branch",
            "cleanup":
                "Delete temporary candidate",
        }

        actual = {}

        for name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                if "CUSTOM_GIT_TOKEN" not in step.get("env", {}):
                    continue

                with self.subTest(job=name):
                    self.assertNotIn(name, actual)

                    actual[name] = step["name"]
                    run = step["run"]

                    self.assertNotIn("${{", run)

                    revision_check = (
                        'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"'
                    )

                    helper = (
                        'source "$GITHUB_WORKSPACE/'
                        '.github/scripts/authenticated-git.sh"'
                    )

                    self.assertLess(
                        run.index(revision_check),
                        run.index(helper),
                    )

                    self.assertIn("authenticated_git push", run)

                    self.assertNotRegex(
                        run,
                        r"(?m)^\s*git\s+push\b",
                    )

        self.assertEqual(actual, expected)

    def test_hardened_shell_interpolation(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        for name in (
            "upstream-support",
            "rebase-candidate",
            "prepare",
            "promote",
        ):
            for step in workflow["jobs"][name]["steps"]:
                if "run" in step:
                    with self.subTest(
                        job=name,
                        step=step.get("name"),
                    ):
                        self.assertNotIn("${{", step["run"])

    def test_rebase_failure_notification(self):
        workflow = load_workflow(
            (WORKFLOWS / "update-custom.yml").read_text()
        )

        notify = workflow["jobs"]["notify"]

        self.assertIn("rebase-candidate", notify["needs"])

        self.assertIn(
            "needs.rebase-candidate.result == 'failure'",
            notify["if"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
