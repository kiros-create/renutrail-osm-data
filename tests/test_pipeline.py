import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pipeline as p

FIXTURE = Path(__file__).parent / "fixtures" / "power.osm"
SOURCE = {"url": p.SOURCE_URL, "asOf": "2026-01-01T00:00:00Z", "sha256": "a" * 64}


def examples():
    return [p.make_feature(f"node/{i}", tags, {"type": "Point", "coordinates": [127 + i / 10, 36]})
            for i, tags in enumerate([
                {"power": "generator", "generator:source": "wind"},
                {"power": "plant", "plant:source": "wind"},
                {"power": "line"}, {"power": "substation"}], 1)]


class PipelineTests(unittest.TestCase):
    def test_selection_and_lifecycle(self):
        self.assertIsNone(p.classify({"power": "minor_line"}))
        self.assertIsNone(p.classify({"power": "generator", "generator:source": "solar"}))
        self.assertEqual(p.classify({"construction:power": "cable"}), ("line", "construction"))
        self.assertEqual(p.classify({"power": "disused", "disused": "line"}), ("line", "disused"))
        self.assertEqual(p.classify({"power": "plant", "plant:source": "wind; solar"}), ("plant", "mapped"))

    def test_no_inferred_operation_or_offshore(self):
        feature = examples()[0]["properties"]
        self.assertEqual(feature["lifecycle"], "mapped")
        self.assertEqual(feature["offshore"], "unknown")
        self.assertIsNone(feature["output"])

    def test_synthetic_osm_nodes_ways_areas_relations(self):
        features, issues, counters = p.extract(FIXTURE)
        self.assertEqual(issues, [])
        self.assertEqual(len(features), 7)
        by_id = {f["id"]: f for f in features}
        self.assertEqual(by_id["way/10"]["properties"]["voltage"], "154000;345000")
        self.assertEqual(by_id["node/5"]["properties"]["offshore"], "yes")
        self.assertEqual(by_id["way/11"]["properties"]["lifecycle"], "disused")
        self.assertEqual(by_id["relation/100"]["geometry"]["type"], "GeometryCollection")
        self.assertEqual(by_id["relation/101"]["geometry"]["type"], "MultiPolygon")
        self.assertNotIn("way/13", by_id)

    def test_duplicate_coordinate_empty_date_and_count_gates(self):
        values = examples()
        counts, issues = p.validate(values, SOURCE)
        self.assertFalse(issues)
        self.assertEqual(counts, dict.fromkeys(p.KINDS, 1))
        self.assertIn("duplicate-id:node/1", p.validate(values + [values[0]], SOURCE)[1])
        broken = copy.deepcopy(values)
        broken[0]["geometry"]["coordinates"] = [float("nan"), 36]
        self.assertTrue(any(i.startswith("invalid-feature") for i in p.validate(broken, SOURCE)[1]))
        self.assertIn("empty-layer:plant", p.validate(values[:1], SOURCE)[1])
        previous = {"source": {**SOURCE, "asOf": "2026-02-01T00:00:00Z"}, "counts": dict.fromkeys(p.KINDS, 10)}
        issues = p.validate(values, SOURCE, previous)[1]
        self.assertIn("source-date-backwards", issues)
        self.assertTrue(any(i.startswith("count-change-over-10-percent") for i in issues))

    def test_tile_split_integrity_and_stable_id(self):
        features = examples()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = p.build_snapshot(features, SOURCE, tmp, max_tile=900)
            self.assertEqual(manifest["snapshotId"], "osm-20260101-aaaaaaaaaaaa")
            self.assertEqual(manifest["quality"]["state"], "passed")
            self.assertFalse(p.verify_assets(tmp, manifest))
            tiles = [a for a in manifest["assets"] if a["kind"] == "tile"]
            self.assertGreater(len(tiles), 1)
            self.assertTrue(all(a["bytes"] <= 900 for a in tiles))
            rows = p.read_json(Path(tmp) / "index.json")["features"]
            self.assertEqual(len({r["id"] for r in rows}), 4)
            self.assertTrue(all((Path(tmp) / r["tile"]).exists() for r in rows))
            (Path(tmp) / "index.json").write_text("tampered")
            self.assertIn("asset-integrity-failed:index.json", p.verify_assets(tmp, manifest))

    def test_first_two_require_distinct_exact_human_reviewed_runs(self):
        def candidate(letter):
            return {"snapshotId": "osm-20260101-" + letter * 12, "quality": {"state": "passed", "issues": []}}
        first = candidate("a")
        self.assertFalse(p.authorize_publication(first, auto_publish=True)[0])
        self.assertFalse(p.authorize_publication(first, approved_snapshot="wrong", actor="owner", run_url="https://github.com/o/r/actions/runs/1")[0])
        self.assertTrue(p.authorize_publication(first, approved_snapshot=first["snapshotId"], actor="owner", run_url="https://github.com/o/r/actions/runs/1", run_id="100")[0])
        second = candidate("a")  # same source hash / snapshot is permitted
        self.assertFalse(p.authorize_publication(second, first, auto_publish=True)[0])
        self.assertTrue(p.authorize_publication(second, first, approved_snapshot=second["snapshotId"], actor="owner", run_url="https://github.com/o/r/actions/runs/2", run_id="101")[0])
        third = candidate("c")
        self.assertFalse(p.authorize_publication(third, second)[0])
        self.assertTrue(p.authorize_publication(third, second, auto_publish=True)[0])
        third["quality"]["state"] = "held"
        self.assertFalse(p.authorize_publication(third, second, auto_publish=True, approved_snapshot=third["snapshotId"], actor="owner", run_url="https://github.com/o/r/actions/runs/3")[0])

    def test_malformed_approval_receipts_do_not_unlock(self):
        candidate = {"snapshotId": "osm-20260101-aaaaaaaaaaaa", "quality": {"state": "passed"}}
        previous = {"publication": {"humanApprovals": [{"snapshotId": "a"}, {"snapshotId": "b"}]}}
        self.assertFalse(p.authorize_publication(candidate, previous, auto_publish=True)[0])

    def test_reapproving_same_run_is_not_two_reviews(self):
        snapshot = {"snapshotId": "osm-20260101-aaaaaaaaaaaa", "quality": {"state": "passed"}}
        p.authorize_publication(snapshot, approved_snapshot=snapshot["snapshotId"], actor="owner",
                                run_url="https://github.com/o/r/actions/runs/1", run_id="10")
        repeated = copy.deepcopy(snapshot)
        p.authorize_publication(repeated, snapshot, approved_snapshot=snapshot["snapshotId"], actor="owner",
                                run_url="https://github.com/o/r/actions/runs/2", run_id="10")
        self.assertEqual(len(repeated["publication"]["humanApprovals"]), 1)
        self.assertFalse(p.authorize_publication(copy.deepcopy(snapshot), repeated, auto_publish=True)[0])

    def test_missing_relation_member_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.osm"
            path.write_text(FIXTURE.read_text().replace('type="node" ref="1" role="generator"',
                                                      'type="node" ref="9999" role="generator"'))
            features, issues, _ = p.extract(path)
            self.assertIn("relation-member-missing:relation/100:n/9999", issues)
            self.assertIn("relation-geometry-unavailable:relation/100", issues)

    def test_single_oversized_feature_is_held_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            features = examples()
            manifest = p.build_snapshot(features, SOURCE, tmp, max_tile=10)
            self.assertEqual(manifest["quality"]["state"], "held")
            self.assertTrue(any(x.startswith("single-feature-over-tile-limit") for x in manifest["quality"]["issues"]))

    def test_exact_10_percent_change_is_permitted(self):
        features = []
        for row in examples():
            for offset in range(11):
                feature = copy.deepcopy(row)
                feature["properties"]["id"] = "node/" + str(int(row["properties"]["id"].split("/")[1]) * 100 + offset)
                features.append(feature)
        previous = {"source": SOURCE, "counts": dict.fromkeys(p.KINDS, 10)}
        self.assertEqual(p.validate(features, SOURCE, previous)[1], [])


if __name__ == "__main__":
    unittest.main()
