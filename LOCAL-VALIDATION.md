# Local validation evidence — 2026-09-10

Status: **automated QA passed; NO human approval or remote publication**.

This is the earlier local-only execution record. For the later repository
setup and two actual GitHub builds, see [CLOUD-VALIDATION.md](CLOUD-VALIDATION.md).
Those builds also have no human approval and did not publish a data Release.

Source: https://download.geofabrik.de/asia/south-korea-latest.osm.pbf

- Size: 286,925,006 bytes; source time: 2026-09-09T20:21:20Z.
- SHA-256: `f3c98b3330c154f70860a799ae0c1b058ad14b49c4c0beb40e14ba29dfc9e68b`.
- Snapshot: `osm-20260909-f3c98b3330c1`.
- 858 generators, 46 plants, 4,134 lines/cables, 1,194 substations.
- 23 assets (index + 22 tiles), 5,507,394 bytes total; index 1,221,372 bytes.
- Quality state `passed`, no geometry/ID/count/integrity issues.
- Wind generators: explicitly offshore 29, unknown 829. Plants: unknown 46.
  No feature has an explicit `offshore=no` tag. This is **not an onshore-only
  inventory** and unknown must not be relabeled onshore.

First optimized execution, `local-validation-1`, generated its manifest at
2026-09-10T08:00:56Z (17:00:56 KST). Its start time was not recorded. The local
run label was assigned for this report, not extracted from a GitHub run.

Second independent execution, `local-validation-2`, started at
2026-09-10T08:05:41Z and completed at 08:06:03Z. It reparsed the same local PBF
with `--force`; no second source download was needed. All 23 data asset hashes
and sizes are identical. Both outputs pass independent on-disk hash checks.
Manifest hashes differ because their `generatedAt` timestamps differ, as expected.

Outputs (gitignored local artifacts):

- `dist/optimized-snapshot/manifest.json` and its adjacent index/tiles.
- `dist/reproduced-snapshot/manifest.json` and its adjacent index/tiles.
- `work/validation-run-2.json`: full per-asset hashes, source hash, run times and
  `approval: NOT_GRANTED`. It contains **no approval receipt**.

Code validation: 14 unittest cases passed; `pip check` reported no broken
requirements; all Python entrypoints compiled. Tests of GitHub publication use
mocked CLI calls only. No repository creation, login inspection, push or Release
API mutation was run. An earlier unoptimized extraction process was stopped
after verifying its exact executable/command; the optimized outputs completed.

To reproduce again into a new directory:

```powershell
.\.venv\Scripts\python.exe reproduce.py --first dist/optimized-snapshot/manifest.json --pbf work/south-korea-latest.osm.pbf --out dist/reproduced-snapshot-3 --report work/validation-run-3.json --second-run-id local-validation-3
```

`reproduce.py` accepts explicit local run labels. Local evidence is not a
GitHub build provenance or human approval.
The weekly public-release gate remains locked until its required real reviews.
