import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p
import release
from test_pipeline import SOURCE, examples


class ReleaseTests(unittest.TestCase):
    def test_reuploaded_approval_artifact_is_not_an_independent_build(self):
        run = {"head_repository": {"full_name": "owner/data"}, "head_branch": "main",
               "path": ".github/workflows/snapshot.yml", "event": "workflow_dispatch",
               "status": "completed", "conclusion": "success", "head_sha": "a" * 40,
               "html_url": "https://github.com/owner/data/actions/runs/100"}
        def fake_gh(*args):
            if args == ("api", "repos/{owner}/{repo}/actions/runs/100"):
                return json.dumps(run)
            if args == ("api", "repos/{owner}/{repo}"):
                return json.dumps({"default_branch": "main"})
            return ""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/data"}), patch.object(release, "gh", side_effect=fake_gh):
            p.write_json(Path(tmp) / "manifest.json", {"build": {"runId": "99", "commitSha": "a" * 40,
                         "workflowRunUrl": "https://github.com/owner/data/actions/runs/99"}})
            with self.assertRaisesRegex(ValueError, "exact run"):
                release.candidate("100", tmp)
            p.write_json(Path(tmp) / "manifest.json", {"build": {"runId": "100", "commitSha": "a" * 40,
                         "workflowRunUrl": run["html_url"]}})
            release.candidate("100", tmp)

    def test_new_snapshot_has_no_fallible_post_publication_receipt_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = p.build_snapshot(examples(), SOURCE, tmp)
            calls = []
            def fake_gh(*args):
                calls.append(args)
                if args[:2] == ("release", "view"):
                    return json.dumps({"assets": [{"name": file.name, "size": file.stat().st_size}
                                      for file in Path(tmp).iterdir() if file.is_file()]})
                return ""
            env = {"AUTO_PUBLISH": "false", "APPROVED_SNAPSHOT_ID": manifest["snapshotId"],
                   "CANDIDATE_RUN_ID": "100", "GITHUB_ACTOR": "human-owner", "GITHUB_SHA": "a" * 40,
                   "WORKFLOW_RUN_URL": "https://github.com/owner/data/actions/runs/101"}
            with patch.dict(os.environ, env, clear=True), patch.object(release, "gh", side_effect=fake_gh), patch("builtins.print"):
                release.publish(tmp)
            self.assertEqual(calls[-1], ("release", "edit", manifest["snapshotId"], "--draft=false", "--latest"))
            self.assertFalse(any(any(str(arg).startswith("osm-review-") for arg in call) for call in calls))
            self.assertEqual(p.read_json(Path(tmp) / "manifest.json")["publication"]["humanApprovals"][0]["runId"], "100")

    def test_same_snapshot_second_review_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate_dir = Path(tmp) / "candidate"
            manifest = p.build_snapshot(examples(), SOURCE, candidate_dir)
            calls = []
            def fake_gh(*args):
                calls.append(args)
                return "[]" if args[0] == "api" else ""
            env = {"AUTO_PUBLISH": "false", "APPROVED_SNAPSHOT_ID": manifest["snapshotId"],
                   "CANDIDATE_RUN_ID": "102", "GITHUB_ACTOR": "human-owner", "GITHUB_SHA": "a" * 40,
                   "WORKFLOW_RUN_URL": "https://github.com/owner/data/actions/runs/103"}
            with patch.dict(os.environ, env, clear=True), patch.object(release, "gh", side_effect=fake_gh), patch("builtins.print"):
                release.publish(candidate_dir, manifest)
            self.assertTrue(any(call[:3] == ("release", "create", "osm-review-102") and "--latest=false" in call for call in calls))
            self.assertFalse(any(call[:2] == ("release", "edit") for call in calls))


if __name__ == "__main__":
    unittest.main()
