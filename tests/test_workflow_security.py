"""Regression checks for security boundaries and workflow dispatch behavior.

These inspect the workflow's indentation scopes and interpret its small
condition-expression subset without executing expressions or requiring PyYAML.
GitHub validates the complete workflow syntax when the file is registered.
"""
import ast
from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[1] / ".github/workflows/snapshot.yml").read_text(encoding="utf-8")


def jobs():
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([a-z_]+):\s*$", body, re.MULTILINE))
    return {match[1]: body[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(body)]
            for index, match in enumerate(matches)}


def steps(job):
    return [part for part in re.split(r"^      - ", job, flags=re.MULTILINE)[1:]]


def condition(block, indent):
    match = re.search(r"^" + " " * indent + r"if: ([^\n]+)(?:\n|$)", block, re.MULTILINE)
    if not match:
        raise AssertionError("Missing explicit condition")
    if match[1].strip() != ">-":
        return match[1].strip()
    continuation = []
    for line in block[match.end():].splitlines():
        if not line.startswith(" " * (indent + 2)):
            break
        continuation.append(line.strip())
    return " ".join(continuation)


def evaluate(expression, context):
    """Evaluate only literals, context lookups, booleans and two Actions calls."""
    expression = expression.replace("&&", " and ").replace("||", " or ")
    def visit(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.Attribute):
            return visit(node.value)[node.attr]
        if isinstance(node, ast.BoolOp):
            values = [bool(visit(value)) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left, right = visit(node.left), visit(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "always" and not node.args:
                return True
            if node.func.id == "format":
                template, *args = [visit(arg) for arg in node.args]
                return template.format(*args)
        raise AssertionError("Unsupported condition expression: " + ast.dump(node))
    return bool(visit(ast.parse(expression, mode="eval").body))


def context(event="workflow_dispatch", operation="build", auto="", result="success", ready="true", branch="main"):
    return {"github": {"event_name": event, "ref": "refs/heads/" + branch,
                       "event": {"repository": {"default_branch": "main"}}},
            "inputs": {"operation": operation}, "vars": {"AUTO_PUBLISH": auto},
            "needs": {"build": {"result": result, "outputs": {"candidate_ready": ready}}}}


class WorkflowSecurityTests(unittest.TestCase):
    def test_only_manual_and_monday_kst_schedule_triggers(self):
        triggers = WORKFLOW.split("\non:\n", 1)[1].split("\n#", 1)[0]
        self.assertEqual(re.findall(r"^  ([a-z_]+):", triggers, re.MULTILINE), ["schedule", "workflow_dispatch"])
        self.assertIn("cron: '17 0 * * 1'", triggers)

    def test_write_permission_is_confined_to_publish_job(self):
        sections = jobs()
        self.assertEqual(set(sections), {"build", "publish"})
        self.assertNotIn("write", WORKFLOW.split("\njobs:\n", 1)[0])
        self.assertIn("      contents: read\n", sections["build"])
        self.assertNotIn("contents: write", sections["build"])
        self.assertIn("      contents: write\n", sections["publish"])
        self.assertEqual(WORKFLOW.count("contents: write"), 1)

    def test_tokens_are_only_in_explicit_github_api_steps(self):
        token_steps = []
        for name, job in jobs().items():
            self.assertNotIn("GH_TOKEN:", job.split("    steps:\n", 1)[0])
            for step in steps(job):
                if "GH_TOKEN:" in step:
                    self.assertRegex(step, r"(?m)^          GH_TOKEN: \$\{\{ github.token \}\}$")
                    self.assertRegex(step, r"python release\.py (previous|candidate|publish) ")
                    self.assertNotIn("pip install", step)
                    self.assertNotIn("pipeline.py", step)
                    token_steps.append(name)
                if "run:" in step:
                    script = step.split("run:", 1)[1]
                    self.assertNotIn("${{", script, "Pass untrusted expressions through env, never shell text")
        self.assertEqual(token_steps.count("build"), 1)
        self.assertEqual(token_steps.count("publish"), 3)
        self.assertEqual(len(token_steps), WORKFLOW.count("GH_TOKEN:"))

    def test_publish_runner_has_no_native_parser_install_or_artifact_upload(self):
        sections = jobs()
        self.assertIn("pipeline.py build", sections["build"])
        self.assertIn("pip install", sections["build"])
        self.assertNotIn("release.py publish", sections["build"])
        for forbidden in ("pip install", "pipeline.py build", "pipeline.py download", "upload-artifact", "cache: pip"):
            self.assertNotIn(forbidden, sections["publish"])
        for job in sections.values():
            for step in steps(job):
                if "uses: actions/checkout@" in step:
                    self.assertIn("persist-credentials: false", step)

    def test_all_actions_are_official_and_commit_pinned(self):
        references = re.findall(r"uses: ([^\s#]+)", WORKFLOW)
        self.assertGreaterEqual(len(references), 6)
        for reference in references:
            self.assertRegex(reference, r"^actions/(checkout|setup-python|upload-artifact|download-artifact)@[0-9a-f]{40}$")

    def test_builds_and_approvals_use_separate_paths(self):
        sections = jobs()
        build_if = condition(sections["build"], 4)
        publish_if = condition(sections["publish"], 4)
        for auto in ("", "false", "true"):
            manual = context(auto=auto)
            self.assertTrue(evaluate(build_if, manual))
            self.assertFalse(evaluate(publish_if, manual), "Manual build must never publish")
        approval = context(operation="approve", result="skipped")
        self.assertFalse(evaluate(build_if, approval))
        self.assertTrue(evaluate(publish_if, approval), "Skipped build must not block exact candidate approval")
        for expression in (build_if, publish_if):
            self.assertFalse(evaluate(expression, context(branch="untrusted")))

    def test_scheduled_publishing_requires_opt_in_and_complete_candidate(self):
        publish_if = condition(jobs()["publish"], 4)
        for auto in ("", "false", "true"):
            for result in ("success", "failure", "cancelled", "skipped"):
                for ready in ("", "true"):
                    with self.subTest(auto=auto, result=result, ready=ready):
                        expected = auto == "true" and result == "success" and ready == "true"
                        self.assertEqual(evaluate(publish_if, context(event="schedule", operation="", auto=auto,
                                                                     result=result, ready=ready)), expected)
        self.assertIn("AUTO_PUBLISH: ${{ vars.AUTO_PUBLISH || 'false' }}", jobs()["publish"])

    def test_approval_restores_original_run_and_never_downloads_current_run(self):
        publish_steps = steps(jobs()["publish"])
        reviewed = next(step for step in publish_steps if "release.py candidate" in step)
        scheduled = next(step for step in publish_steps if "uses: actions/download-artifact@" in step)
        approval = context(operation="approve", result="skipped")
        self.assertTrue(evaluate(condition(reviewed, 8), approval))
        self.assertFalse(evaluate(condition(scheduled, 8), approval))
        self.assertIn('--run-id "$CANDIDATE_RUN_ID"', reviewed)
        self.assertNotIn("run-id:", scheduled, "Automatic publication must use only the current build artifact")
        self.assertIn("name: osm-candidate", scheduled)
        self.assertIn("CANDIDATE_RUN_ID: ${{ inputs.candidate_run_id || '' }}", jobs()["publish"])


if __name__ == "__main__":
    unittest.main()
