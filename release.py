"""GitHub CLI glue. No remote operation is performed merely by importing this module."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from pipeline import (authorize_publication, read_json, validate, verify_assets, write_json)


def gh(*args):
    result = subprocess.run(["gh", *args], check=True, text=True, capture_output=True)
    return result.stdout


def previous_manifest(destination):
    # Failure is not silently treated as first run: distinguish no releases
    # from authentication/network/permissions failures using the list endpoint.
    pages = json.loads(gh("api", "repos/{owner}/{repo}/releases", "--paginate", "--slurp"))
    releases = [release for page in pages for release in page]
    valid = [r for r in releases if not r["draft"] and not r["prerelease"]
             and re.fullmatch(r"osm-\d{8}-[0-9a-f]{12}", r["tag_name"])]
    if not valid:
        return None
    latest = max(valid, key=lambda r: r["published_at"])
    Path(destination).mkdir(parents=True, exist_ok=True)
    gh("release", "download", latest["tag_name"], "--pattern", "manifest.json", "--dir", str(destination))
    baseline_path = Path(destination) / "manifest.json"
    baseline = read_json(baseline_path)
    receipts = list(baseline.get("publication", {}).get("humanApprovals", []))
    # Human review of a second reproduction need not mutate a published data
    # snapshot. Review receipts are separate non-latest GitHub Releases.
    reviews = [r for r in releases if not r["draft"] and not r["prerelease"]
               and re.fullmatch(r"osm-review-[1-9][0-9]*", r["tag_name"])]
    for review in reviews:
        folder = Path(destination) / review["tag_name"]
        folder.mkdir()
        gh("release", "download", review["tag_name"], "--pattern", "approval.json", "--dir", str(folder))
        receipt = read_json(folder / "approval.json")
        if review["tag_name"] != "osm-review-" + str(receipt.get("runId", "")):
            raise ValueError("Review tag and receipt run ID differ")
        receipts.append(receipt)
    baseline.setdefault("publication", {})["humanApprovals"] = receipts
    write_json(baseline_path, baseline)
    return str(Path(destination) / "manifest.json")


def candidate(run_id, directory):
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Candidate run ID must be numeric")
    run = json.loads(gh("api", "repos/{owner}/{repo}/actions/runs/" + run_id))
    repo = os.environ["GITHUB_REPOSITORY"]
    default_branch = json.loads(gh("api", "repos/{owner}/{repo}"))["default_branch"]
    if (run["head_repository"]["full_name"] != repo or run["head_branch"] != default_branch
            or run["path"] != ".github/workflows/snapshot.yml"
            or run["event"] not in ("schedule", "workflow_dispatch")
            or run["status"] != "completed" or run["conclusion"] != "success"):
        raise ValueError("Candidate must come from this repository's successful trusted workflow on its default branch")
    gh("run", "download", run_id, "--name", "osm-candidate", "--dir", str(directory))
    manifest = read_json(Path(directory) / "manifest.json")
    provenance = manifest.get("build", {})
    if (provenance.get("runId") != run_id or provenance.get("commitSha") != run["head_sha"]
            or provenance.get("workflowRunUrl") != run["html_url"]):
        raise ValueError("Artifact must be built by this exact run, not reuploaded by an approval run")


def publish_review_receipt(directory, manifest, run_id):
    receipt = next(item for item in manifest["publication"]["humanApprovals"] if item["runId"] == run_id)
    folder = Path(directory).parent / "review-receipt"
    path = folder / "approval.json"
    write_json(path, receipt)
    tag = "osm-review-" + run_id
    releases = json.loads(gh("api", "repos/{owner}/{repo}/releases?per_page=100"))
    if any(r["tag_name"] == tag for r in releases):
        print("Review receipt already exists; never overwrite")
        return
    gh("release", "create", tag, str(path), "--target", os.environ["GITHUB_SHA"], "--latest=false",
       "--title", "Human review of " + manifest["snapshotId"],
       "--notes", "Explicit human review record for build run " + run_id + ". Not a data snapshot.")


def publish(directory, previous=None):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    problems = verify_assets(directory, manifest)
    if problems:
        raise ValueError(";".join(problems))
    # Revalidate a reviewed artifact against the LATEST release, not only the
    # baseline that existed when it was built. Held candidates cannot bypass QA.
    features = []
    for asset in manifest["assets"]:
        if asset["kind"] == "tile":
            features.extend(read_json(directory / asset["name"])["features"])
    counts, issues = validate(features, manifest["source"], previous, manifest["quality"]["issues"])
    if counts != manifest["counts"]:
        issues.append("manifest-count-mismatch")
    if issues:
        raise ValueError("Publication held: " + ";".join(issues))
    run_id = os.environ.get("CANDIDATE_RUN_ID", "") or os.environ.get("GITHUB_RUN_ID", "")
    allowed, reason = authorize_publication(
        manifest, previous, os.environ.get("AUTO_PUBLISH", "false").lower() == "true",
        os.environ.get("APPROVED_SNAPSHOT_ID", ""), os.environ.get("GITHUB_ACTOR", ""),
        os.environ.get("WORKFLOW_RUN_URL", ""), run_id)
    if not allowed:
        print("Publication held: " + reason)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
                output.write(f"\nPublication held: **{reason}**. Review candidate `{manifest['snapshotId']}`.\n")
        return
    write_json(directory / "manifest.json", manifest)
    if previous and previous["snapshotId"] == manifest["snapshotId"]:
        if manifest["publication"]["mode"] == "human":
            publish_review_receipt(directory, manifest, run_id)
        print("Snapshot already published; data unchanged, explicit review recorded")
        return
    tag = manifest["snapshotId"]
    files = [str(directory / "manifest.json"), *(str(directory / a["name"]) for a in manifest["assets"])]
    if len(files) > 1000:
        raise ValueError("Too many release assets")
    # Draft remains unpublished if upload/check fails. Never edit/clobber an
    # existing tag or published asset. All filenames were allowlist-validated.
    gh("release", "create", tag, "--draft", "--target", os.environ["GITHUB_SHA"],
       "--title", tag, "--notes", "OpenStreetMap derivative dataset, ODbL 1.0. © OpenStreetMap contributors. "
       "See manifest.json for source timestamp, integrity checks, QA and approval records. "
       "https://www.openstreetmap.org/copyright")
    for offset in range(0, len(files), 50):
        gh("release", "upload", tag, *files[offset:offset + 50])
    uploaded = json.loads(gh("release", "view", tag, "--json", "assets"))["assets"]
    actual = {a["name"]: a["size"] for a in uploaded}
    expected = {Path(f).name: Path(f).stat().st_size for f in files}
    if actual != expected:
        raise ValueError("Draft upload assets differ; draft retained for diagnosis")
    gh("release", "edit", tag, "--draft=false", "--latest")
    # New snapshots already contain this approval receipt in their immutable
    # manifest. No fallible post-publication operation changes job status.
    print("Published " + tag)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["previous", "candidate", "publish"])
    parser.add_argument("--dir", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--previous")
    args = parser.parse_args()
    if args.command == "previous":
        previous = previous_manifest(args.dir)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("path=" + (previous or "") + "\n")
    elif args.command == "candidate":
        candidate(args.run_id, args.dir)
    else:
        publish(args.dir, read_json(args.previous))


if __name__ == "__main__":
    main()
