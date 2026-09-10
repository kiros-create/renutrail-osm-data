# RenuTrail OSM infrastructure snapshots

An independent, openly reusable South Korea infrastructure dataset. This
repository is **data tooling, not the RenuTrail website**. It does not deploy to
Sites or GitHub Pages, use hosting secrets, scrape map tiles, query Overpass, or
collect application/customer data. Python 3.12 and pinned pyosmium parse the
public [Geofabrik PBF](https://download.geofabrik.de/asia/south-korea.html)
offline. New code and synthetic fixtures use MIT; generated data uses
[ODbL 1.0](DATA-LICENSE.md), © OpenStreetMap contributors.

## What is included

- Wind `power=generator` and `power=plant`, selected using the corresponding
  `generator:source` / `plant:source` tag containing `wind`.
- `power=line`, `power=cable`, and `power=substation` (including lifecycle
  prefixed equivalents). `minor_line` and `minor_cable` are excluded.
- Nodes, ways, closed areas / multipolygons and supported `type=site` relations.
  Site relations retain full member geometry in a GeoJSON GeometryCollection.
- Raw voltage/output strings, nullable names, and allowlisted descriptive tags.
  No OSM contributor username, user ID or changeset ID is retained.

`lifecycle=mapped` means only that no lifecycle tag was found; **not operating**.
Construction, proposed, disused, abandoned, demolished and razed tags remain
distinct. `offshore` is `yes` / `no` only when explicitly tagged; otherwise
`unknown`. No sea/land inference or permitting inference is made. Generator
and plant features are different objects and their outputs must not be added
together as independent capacity. The data is incomplete community mapping,
not KEPCO's official network or evidence of available grid connection capacity.

## Local commands (no publication)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe pipeline.py download --pbf work/south-korea.osm.pbf
.\.venv\Scripts\python.exe pipeline.py build --pbf work/south-korea.osm.pbf --out dist/candidate
```

Use an actual Python 3.12 executable, not a Windows Store alias if it is not
configured. On Linux use `.venv/bin/python` instead. The source download is
bounded to 1GB, 15 minutes per attempt, three attempts and 45-second read
timeouts. Its fixed HTTPS host is checked. Source SHA-256 and the PBF header's
replication timestamp are recorded; download time never substitutes for the
source timestamp. Supply `--previous path/to/manifest.json` to both commands
to skip extraction when the PBF hash is unchanged and compare against the last
published snapshot. Output directories must be fresh; existing manifests are
not overwritten. Raw PBFs, environments, temporary files and generated output
are gitignored and raw PBFs are never release assets.

## Public schema v1

`manifest.json`:

```json
{
  "schemaVersion": 1,
  "snapshotId": "osm-YYYYMMDD-12hex",
  "source": {"url": "Geofabrik HTTPS PBF URL", "asOf": "UTC ISO timestamp", "sha256": "64hex"},
  "generatedAt": "UTC ISO timestamp",
  "counts": {"generator": 0, "plant": 0, "line": 0, "substation": 0},
  "quality": {"state": "passed", "issues": []},
  "assets": [{"name": "index.json", "sha256": "64hex", "bytes": 0, "kind": "index"}]
}
```

The displayed values above are schema illustrations, not an approved snapshot.
`snapshotId` uses **source** UTC date plus the first 12 source-hash characters.
Optional `license`, `diagnostics` and, only after authorization, `publication`
fields preserve provenance and real approval receipts.

`index.json` is `{ "features": [...] }`, with rows:
`{id,kind,name,lon,lat,voltage,output,offshore,lifecycle,tile}`.
IDs are stable `node/123`, `way/123`, or `relation/123`; different object types
must not be conflated. Nullable descriptive fields stay null, never zero.

Tile assets are GeoJSON FeatureCollections with full geometry and index
properties plus allowlisted `tags`. Each feature belongs to **one 1-degree
cell, based on the center of its geometry bounding box**, not clipped geometry.
Its anchor is representative, not a surveyed turbine/substation location.
Tile `bbox` in the manifest is the **actual union of member geometry extents**,
which may cross the cell boundary. Consumers must load intersecting manifest
bounding boxes, not assume a long transmission line stays within its grid
cell. Oversized cells are split deterministically by sorted ID until each
tile is at most 2,000,000 bytes. A single feature above that limit is held,
not silently simplified or truncated. The index maximum is 12,000,000 bytes.
Total release assets, including manifest, cannot exceed 1000.

## QA and diagnostics

Publication is held on duplicate IDs, invalid/nonfinite coordinates, missing
geometry or relation references, any empty required layer, source date moving
backwards, future/missing source time, unexpected URL/hash, over 10% relative
count change in **any** layer, excessive asset counts or oversized data.
Exactly 10% change is allowed. A prior zero followed by nonzero is held.
Structural QA `passed` is **not human review** and is not confirmation that
any facility is operational, complete, onshore, permitted or connectable.

`quality.issues` lists failures; `diagnostics` reports extracted object and
geometry-failure counters. A publication step rechecks asset hashes/sizes,
geometry, IDs/counts and the latest published baseline. Failed jobs or held
candidates leave the last published release unchanged. A failed upload leaves
an unpublished draft for diagnosis; this code never overwrites existing tags.
Quality-held datasets require correcting source/logic and rebuilding, not a
generic approval that bypasses QA.

## GitHub-hosted weekly operation and explicit approval

The included workflow runs Monday **00:17 UTC (09:17 KST)** and offers manual
`workflow_dispatch`. It uses GitHub-hosted Ubuntu, so the user's PC and Codex
need not be running. The public repository is
[kiros-create/renutrail-osm-data](https://github.com/kiros-create/renutrail-osm-data).
The workflow is installed and two independent cloud builds passed on
2026-09-10. All 23 data asset hashes match each other and the local baseline;
see [cloud validation and human review checklist](CLOUD-VALIDATION.md).
The owner confirmed review of both candidate map results on 2026-09-10.
The first data Release and the second same-snapshot review receipt are public;
`AUTO_PUBLISH=true` is now configured for quality-passed scheduled releases.
The first scheduled event has not yet occurred. These reviews cover the initial
map presentation, not nationwide facility status or permit verification.

1. Keep repository variable `AUTO_PUBLISH` unset or `false` (the default).
2. Run operation `build`. A valid candidate is retained as the `osm-candidate`
   workflow artifact for 14 days; its exact snapshot ID and QA appear in the
   workflow summary. Inspect/download its index, tiles, provenance and map.
   Manual `build` only creates a candidate and skips the publishing job, even
   when `AUTO_PUBLISH=true`. A quality-held candidate is still reviewable;
   a successful build alone does not authorize publication.
3. After a person reviews it, run operation `approve`, supplying that successful
   build's `candidate_run_id` and exact `approved_snapshot_id`. A fresh latest
   download is **not** substituted for the artifact the person reviewed.
4. Candidate artifacts must come from this repository's successful trusted
   workflow on the default branch. Build run ID, source commit and workflow URL
   must match the artifact's build provenance; an approval run cannot be counted
   as a new build by reuploading the same artifact. Approval runs do not upload
   candidate artifacts. QA is rerun against the newest publication.
   Approval stores the actual GitHub actor, time, snapshot ID and workflow URL;
   it is never inferred from a prior chat, QA pass or scheduled run.
5. The first **two distinct build runs** require this human path even if someone
   sets `AUTO_PUBLISH=true`. After two real receipts, setting that variable to
   `true` enables later quality-passed scheduled releases. Both reviewed runs may
   use the same PBF/snapshot ID; manual builds deliberately reproduce unchanged
   source while scheduled builds skip it. No two-week wait is required.
   New snapshot manifests retain the corresponding review receipt. A second
   review of the same snapshot is retained as a separate
   `osm-review-<buildRunId>` Release with `approval.json`; this never replaces
   the latest data Release, and
   published snapshot assets are never overwritten. Reapproving the same build
   run does not count twice. Leave `AUTO_PUBLISH=false` for
   continued manual publication. Repository owners must protect the default
   branch and workflow editing: the repository's own published approval history
   is trusted, not a cryptographic statement immune to an owner's changes.

The workflow publishes **GitHub Releases only**, first creating an unpublished
draft, uploading named manifest/assets, checking uploaded sizes, then publishing
the release. It neither creates a Pages deployment nor pushes data into source
Git history. Consumers should fetch manifest and assets from the **same immutable
version tag**, verify integrity, retain their last good snapshot on failure,
and explicitly show source age / pending human review. A small same-origin
server proxy in the existing site can cache only allowlisted release resources;
that proxy is outside this repository. Do not assume browser CORS works without
testing. No hosting token is needed by this public-data consumer.

GitHub scheduling is not a real-time guarantee: runs can be delayed/dropped and
public-repository schedules are disabled after 60 days without repository
activity. Workflow execution alone must not be assumed to prevent that. Monitor
source age and workflow status; re-enable a disabled workflow or change its
cron as an authorized maintainer, then run a manual build. Do not fabricate
activity or approval commits merely to evade inactivity limits.

## Cost and hosting constraints (checked 2026-09-10)

- Public repositories using standard GitHub-hosted runners have free execution;
  larger runners and stored artifacts/caches have separate billing conditions.
  Candidates expire after 14 days; the raw PBF is not cached/uploaded.
- Releases support up to 1000 assets, each under 2GiB; GitHub documents no total
  release-size or bandwidth limit. This is not a commercial availability SLA.
- GitHub Pages business/SaaS restrictions are why this project does not use it.
  This release project distributes freely reusable OSM data and source code,
  not a private/commercial API. Applicable GitHub terms still apply.

Official references:

- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.osmcode.org/pyosmium/latest/user_manual/03-Working-with-Geometries/
- https://download.geofabrik.de/asia/south-korea.html

## Security and reproducibility

No password/PAT is stored; the workflow uses its ephemeral repository-scoped
`GITHUB_TOKEN`. Environment files and common private-key/credential files are
gitignored; inspect the exact staged files before publishing because ignore
rules do not remove files that were already tracked. No user input is
interpolated into executable shell syntax.

The build job has read-only repository permissions. Dependency installation,
source download and native PBF parsing have no `GH_TOKEN` environment variable.
Publishing uses a separate runner with a fresh source checkout and the Python
standard library; it does not install dependencies or parse PBF files. Only
that job grants `contents:write`. `GH_TOKEN` is passed only to individual
GitHub API/release steps, and both checkouts disable persisted credentials.
The GitHub Actions runner still provides its own action/runtime credentials;
step-scoped environment variables are not a sandbox against malicious actions.

All official actions are pinned to full commit SHAs, verified against their
upstream release tags. Python dependencies are version-pinned binary wheels;
wheel hashes are not yet locked. `tests/test_workflow_security.py` checks
credential scopes, job permissions, action pins and publication conditions.
Updating dependencies or workflow controls requires the fixture, approval and
workflow security tests to pass.

Publication is limited to the default-branch workflow, triggered only by the
schedule or a manual dispatch; there are no push or PR triggers. The
publication code still requires the first two distinct human-reviewed build
runs before automatic publishing can proceed. On 2026-09-10 the repository's
active default-branch ruleset was verified to block force pushes and branch
deletion, with no bypass entries. Secret Protection and push protection are
enabled. These are account-side settings, not controls installed by this file.
Required PR review / required review for workflow changes is not enabled, and
trusted owners can still change normal source commits and repository settings.
Restrict write access to trusted maintainers. Never label this repository's
automated checks as human legal or engineering approval.
