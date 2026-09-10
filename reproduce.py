"""Run a second local reproduction and record evidence, NEVER approval."""
import argparse
from pathlib import Path
import subprocess
import sys

from pipeline import read_json, sha256_file, utcnow, verify_assets, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", required=True, help="First manifest path")
    parser.add_argument("--pbf", required=True)
    parser.add_argument("--out", required=True, help="Fresh second output directory")
    parser.add_argument("--report", required=True)
    parser.add_argument("--first-run-id", default="local-validation-1")
    parser.add_argument("--second-run-id", default="local-validation-2")
    args = parser.parse_args()
    first_path, second_dir = Path(args.first).resolve(), Path(args.out).resolve()
    first = read_json(first_path)
    started = utcnow()
    command = [sys.executable, str(Path(__file__).with_name("pipeline.py")), "build", "--pbf",
               str(Path(args.pbf).resolve()), "--out", str(second_dir), "--previous", str(first_path), "--force"]
    run = subprocess.run(command, text=True, capture_output=True)
    if run.returncode:
        write_json(args.report, {"type": "local-reproducibility-evidence", "approval": "NOT_GRANTED",
                   "runId": args.second_run_id, "startedAt": started, "finishedAt": utcnow(),
                   "state": "failed", "returnCode": run.returncode, "stderr": run.stderr})
        sys.stderr.write(run.stderr)
        return run.returncode
    second_path = second_dir / "manifest.json"
    second = read_json(second_path)
    first_assets = {asset["name"]: asset for asset in first["assets"]}
    second_assets = {asset["name"]: asset for asset in second["assets"]}
    comparisons = []
    for name in sorted(first_assets.keys() | second_assets.keys()):
        left, right = first_assets.get(name), second_assets.get(name)
        comparisons.append({"name": name, "firstSha256": left["sha256"] if left else None,
                            "secondSha256": right["sha256"] if right else None,
                            "bytes": right["bytes"] if right else None,
                            "equal": bool(left and right and left["sha256"] == right["sha256"] and left["bytes"] == right["bytes"])})
    integrity = {"first": verify_assets(first_path.parent, first), "second": verify_assets(second_dir, second)}
    source_hash = sha256_file(args.pbf)
    equal_source = first["source"] == second["source"] and source_hash == second["source"]["sha256"]
    passed = (all(x["equal"] for x in comparisons) and bool(comparisons) and equal_source
              and not any(integrity.values()) and first["counts"] == second["counts"]
              and first["quality"]["state"] == second["quality"]["state"] == "passed")
    report = {
        "type": "local-reproducibility-evidence", "approval": "NOT_GRANTED",
        "note": "Local run labels identify executions, not GitHub build runs or human approvals. First start time was not recorded. Manifest hashes may differ because generatedAt differs.",
        "firstRun": {"runId": args.first_run_id, "startedAt": None, "generatedAt": first["generatedAt"],
                     "manifest": str(first_path), "manifestSha256": sha256_file(first_path)},
        "secondRun": {"runId": args.second_run_id, "startedAt": started, "generatedAt": second["generatedAt"],
                      "finishedAt": utcnow(), "manifest": str(second_path), "manifestSha256": sha256_file(second_path)},
        "snapshotId": second["snapshotId"], "source": second["source"], "sourceHashEqual": equal_source,
        "counts": second["counts"], "quality": second["quality"], "integrityIssues": integrity,
        "assets": comparisons, "assetCount": len(comparisons), "allAssetHashesEqual": all(x["equal"] for x in comparisons),
        "state": "passed" if passed else "failed", "command": command,
    }
    write_json(args.report, report)
    print({"state": report["state"], "assetCount": report["assetCount"],
           "allAssetHashesEqual": report["allAssetHashesEqual"], "report": str(Path(args.report).resolve())})
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
