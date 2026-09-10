"""Bounded, offline OSM power extraction; code MIT, generated OSM data ODbL.

The downloader uses the public Geofabrik extract, never Overpass/OSM tiles.
No credentials or OSM contributor personal metadata are collected.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

SOURCE_URL = "https://download.geofabrik.de/asia/south-korea-latest.osm.pbf"
KINDS = ("generator", "plant", "line", "substation")
LIFECYCLES = ("construction", "proposed", "disused", "abandoned", "demolished", "razed")
MAX_DOWNLOAD = 1_000_000_000
MAX_TILE = 2_000_000
MAX_ASSETS = 999  # manifest itself is the 1000th asset at most.
MAX_INDEX = 12_000_000
UA = "RenuTrail-OSM-Data/1.0 (+https://www.openstreetmap.org/copyright)"


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_date(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must contain a timezone")
    return parsed.astimezone(timezone.utc)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(value)
    part = path.with_suffix(path.suffix + ".part")
    part.write_bytes(data)
    part.replace(path)
    return data


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_pbf(destination, previous=None, retries=3, max_bytes=MAX_DOWNLOAD):
    """Fixed HTTPS source, bounded size/time/retries; atomic destination replace."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(retries):
        total, digest, started = 0, hashlib.sha256(), time.monotonic()
        try:
            req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as response, part.open("wb") as out:
                # urllib follows redirects; reject a redirect outside this host.
                if not response.url.startswith("https://download.geofabrik.de/"):
                    raise ValueError("Unexpected download redirect")
                expected = int(response.headers.get("Content-Length", "0"))
                if expected > max_bytes:
                    raise ValueError("Source exceeds the configured download limit")
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > max_bytes or time.monotonic() - started > 900:
                        raise ValueError("Download exceeded size/time limit")
                    digest.update(block)
                    out.write(block)
                if not total or (expected and total != expected):
                    raise ValueError("Empty or truncated source")
            result = {"url": SOURCE_URL, "sha256": digest.hexdigest(), "bytes": total}
            part.replace(destination)
            result["unchanged"] = bool(previous and previous.get("source", {}).get("sha256") == result["sha256"])
            return result
        except (OSError, ValueError, urllib.error.URLError):
            if attempt + 1 == retries:
                raise
            time.sleep(min(2 ** attempt, 4))
    raise RuntimeError("Download did not complete")


def classify(tags):
    """Return (kind, lifecycle), retaining life-cycle prefixes and unknowns."""
    power, lifecycle = tags.get("power"), "mapped"
    for state in LIFECYCLES:
        if tags.get(state + ":power"):
            power, lifecycle = tags[state + ":power"], state
            break
    if power in LIFECYCLES:
        lifecycle, power = power, tags.get(power)
    if lifecycle == "mapped":
        for state in LIFECYCLES:
            if tags.get(state) == "yes":
                lifecycle = state
                break
    if power in ("generator", "plant"):
        sources = tags.get(power + ":source", "").lower().split(";")
        if "wind" not in [s.strip() for s in sources]:
            return None
        return power, lifecycle
    if power in ("line", "cable"):
        return "line", lifecycle
    if power == "substation":
        return power, lifecycle
    return None


def coordinates(geometry):
    if geometry["type"] == "GeometryCollection":
        for child in geometry["geometries"]:
            yield from coordinates(child)
        return
    def flatten(value):
        if isinstance(value, (tuple, list)) and len(value) >= 2 and all(
            isinstance(v, (int, float)) for v in value[:2]
        ):
            yield value[:2]
        else:
            for item in value:
                yield from flatten(item)
    yield from flatten(geometry.get("coordinates", []))


def bounds(geometry):
    values = list(coordinates(geometry))
    if not values:
        raise ValueError("Empty geometry")
    for lon, lat in values:
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError("Invalid WGS84 coordinate")
    return [min(p[0] for p in values), min(p[1] for p in values),
            max(p[0] for p in values), max(p[1] for p in values)]


def merge_bounds(items):
    return [min(x[0] for x in items), min(x[1] for x in items),
            max(x[2] for x in items), max(x[3] for x in items)]


def make_feature(identifier, tags, geometry):
    category = classify(tags)
    if not category:
        return None
    kind, lifecycle = category
    bbox = bounds(geometry)
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    offshore = tags.get("offshore", "unknown")
    if offshore not in ("yes", "no"):
        offshore = "unknown"
    props = {
        "id": identifier, "kind": kind, "name": tags.get("name:ko") or tags.get("name") or None,
        "lon": lon, "lat": lat, "voltage": tags.get("voltage") or None,
        "output": tags.get(kind + ":output:electricity") or None,
        "offshore": offshore, "lifecycle": lifecycle, "tile": "",
    }
    # Allowlisted descriptive tags only, no OSM user/uid/changeset metadata.
    allowed = {"power", "generator:source", "generator:method", "generator:type",
               "plant:source", "circuits", "cables", "frequency", "substation", "location",
               "operator", "offshore", "voltage", "generator:output:electricity", "plant:output:electricity"}
    allowed.update(state + ":power" for state in LIFECYCLES)
    return {"type": "Feature", "id": identifier, "bbox": bbox,
            "properties": {**props, "tags": {k: v for k, v in tags.items() if k in allowed}},
            "geometry": geometry}


def extract(pbf):
    import osmium
    issues, counters = [], Counter()
    features, relations, needed, member_geometries = {}, {}, set(), {}
    # First bounded-memory pass: only relevant relation metadata and references.
    for obj in osmium.FileProcessor(str(pbf), osmium.osm.RELATION):
        tags = dict(obj.tags)
        if not classify(tags):
            continue
        rid = f"relation/{obj.id}"
        members = [(str(m.type), m.ref) for m in obj.members]
        relations[rid] = {"tags": tags, "members": members}
        needed.update(members)
    factory = osmium.geom.GeoJSONFactory()
    power_keys = ["power", *(state + ":power" for state in LIFECYCLES)]
    # C++ filters run AFTER location caching. Do not iterate tens of millions
    # of unrelated supporting nodes through Python just to reject their tags.
    primary = (osmium.FileProcessor(str(pbf)).with_locations()
               .with_areas(osmium.filter.KeyFilter(*power_keys))
               .with_filter(osmium.filter.KeyFilter(*power_keys)))
    streams = [(primary, False)]
    if needed:
        member_ids = {ref for _, ref in needed}
        member_ids.update(2 * ref for typ, ref in needed if typ == "w")
        member_ids.update(2 * ref + 1 for typ, ref in needed if typ == "r")
        members = (osmium.FileProcessor(str(pbf)).with_locations()
                   .with_areas(osmium.filter.IdFilter([ref for typ, ref in needed if typ == "r"]))
                   .with_filter(osmium.filter.IdFilter(member_ids)))
        streams.append((members, True))
    # A second compiled-ID pass recovers untagged site members without dropping
    # the reference nodes needed to form ways. Area IDs differ from OSM IDs.
    for obj, member_pass in ((obj, is_member) for processor, is_member in streams for obj in processor):
        if obj.is_relation():
            counters["relationsRead"] += 1
            continue
        if obj.is_area():
            original_type = "way" if obj.from_way() else "relation"
            identifier = f"{original_type}/{obj.orig_id()}"
            member_key = ("w" if obj.from_way() else "r", obj.orig_id())
        else:
            original_type = "node" if obj.is_node() else "way"
            identifier = f"{original_type}/{obj.id}"
            member_key = ("n" if obj.is_node() else "w", obj.id)
        tags = dict(obj.tags)
        selected = None if member_pass else classify(tags)
        if not selected and member_key not in needed:
            continue
        counters["candidateArea" if obj.is_area() else "candidate" + original_type.title()] += bool(selected)
        try:
            if obj.is_area():
                geometry = json.loads(factory.create_multipolygon(obj))
            elif obj.is_node():
                if not obj.location.valid():
                    raise ValueError("Missing node location")
                geometry = json.loads(factory.create_point(obj))
            else:
                if any(not n.location.valid() for n in obj.nodes):
                    raise ValueError("Missing referenced node")
                pts = [[n.lon, n.lat] for n in obj.nodes]
                if len(pts) < 2:
                    raise ValueError("Way has fewer than two points")
                if selected and selected[0] in ("plant", "substation", "generator") and obj.is_closed() and len(pts) >= 4:
                    geometry = {"type": "Polygon", "coordinates": [pts]}
                else:
                    geometry = {"type": "LineString", "coordinates": pts}
            bounds(geometry)
            if member_key in needed and (member_key not in member_geometries or obj.is_area()):
                member_geometries[member_key] = geometry
            if selected:
                if identifier in features and not obj.is_area():
                    issues.append("duplicate-source-id:" + identifier)
                features[identifier] = make_feature(identifier, tags, geometry)
        except (ValueError, RuntimeError, osmium.InvalidLocationError) as exc:
            issues.append(f"geometry-failure:{identifier}:{type(exc).__name__}")
            counters["geometryFailures"] += 1
    # Site relations aren't polygons: retain member geometry as a collection.
    # Recursion is bounded; nested cycles/missing references become held issues.
    def assemble(rid, stack=()):
        if rid in features:
            return features[rid]["geometry"]
        if rid in stack or len(stack) > 8 or rid not in relations:
            return None
        data = relations[rid]
        if data["tags"].get("type") != "site":
            return None
        children = []
        for typ, ref in data["members"]:
            geometry = member_geometries.get((typ, ref))
            if geometry is None and typ == "r":
                geometry = assemble(f"relation/{ref}", (*stack, rid))
            if geometry is None:
                issues.append(f"relation-member-missing:{rid}:{typ}/{ref}")
            else:
                children.append(geometry)
        if not children:
            return None
        geometry = {"type": "GeometryCollection", "geometries": children}
        features[rid] = make_feature(rid, data["tags"], geometry)
        return geometry
    for rid in relations:
        if assemble(rid) is None:
            issues.append("relation-geometry-unavailable:" + rid)
    return list(features.values()), sorted(set(issues)), dict(counters)


def source_metadata(pbf, source_url=SOURCE_URL):
    import osmium
    with osmium.io.Reader(str(pbf)) as reader:
        header = reader.header()
        asof = header.get("osmosis_replication_timestamp")
    if not asof:
        raise ValueError("PBF lacks source replication timestamp; do not substitute download time")
    parse_date(asof)
    return {"url": source_url, "asOf": asof, "sha256": sha256_file(pbf)}


def validate(features, source, previous=None, extraction_issues=()):
    issues, identifiers, counts = list(extraction_issues), set(), Counter()
    for feature in features:
        try:
            props = feature["properties"]
            identifier = props["id"]
            if not re.fullmatch(r"(node|way|relation)/[1-9][0-9]*", identifier):
                issues.append("invalid-id:" + str(identifier))
            if identifier in identifiers:
                issues.append("duplicate-id:" + identifier)
            identifiers.add(identifier)
            bounds(feature["geometry"])
            lon, lat = props["lon"], props["lat"]
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
                issues.append("invalid-anchor:" + identifier)
            if props["kind"] not in KINDS:
                issues.append("invalid-kind:" + identifier)
            counts[props["kind"]] += 1
        except (KeyError, ValueError, TypeError) as exc:
            issues.append("invalid-feature:" + type(exc).__name__)
    for kind in KINDS:
        if not counts[kind]:
            issues.append("empty-layer:" + kind)
    try:
        asof = parse_date(source["asOf"])
        if asof > datetime.now(timezone.utc):
            issues.append("future-source-date")
        if previous:
            if asof < parse_date(previous["source"]["asOf"]):
                issues.append("source-date-backwards")
            for kind in KINDS:
                before = previous.get("counts", {}).get(kind, 0)
                after = counts[kind]
                if (before == 0 and after != 0) or (before and abs(after - before) / before > 0.10):
                    issues.append(f"count-change-over-10-percent:{kind}:{before}->{after}")
    except (KeyError, ValueError, TypeError):
        issues.append("invalid-source-date")
    if source.get("url") != SOURCE_URL:
        issues.append("unexpected-source-url")
    if not re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")):
        issues.append("invalid-source-hash")
    return {kind: counts[kind] for kind in KINDS}, sorted(set(issues))


def build_snapshot(features, source, destination, previous=None, extraction_issues=(), diagnostics=None, max_tile=MAX_TILE):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "manifest.json").exists():
        raise ValueError("Destination already has a manifest; choose a fresh directory")
    counts, issues = validate(features, source, previous, extraction_issues)
    snapshot_id = "osm-" + parse_date(source["asOf"]).strftime("%Y%m%d") + "-" + source["sha256"][:12]
    groups, assets, index = defaultdict(list), [], []
    for feature in sorted(features, key=lambda f: f["properties"]["id"]):
        p = feature["properties"]
        groups[(math.floor(p["lon"]), math.floor(p["lat"]))].append(feature)
    def emit(group, cell, suffix=""):
        name = f"tile-{cell[0]}-{cell[1]}{suffix}.geojson"
        for feature in group:
            feature["properties"]["tile"] = name
        collection = {"type": "FeatureCollection", "features": group}
        data = json_bytes(collection)
        if len(data) > max_tile and len(group) > 1:
            halfway = len(group) // 2
            emit(group[:halfway], cell, suffix + "a")
            emit(group[halfway:], cell, suffix + "b")
            return
        if len(data) > max_tile:
            issues.append("single-feature-over-tile-limit:" + group[0]["properties"]["id"])
        (destination / name).write_bytes(data)
        assets.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                       "bytes": len(data), "kind": "tile", "bbox": merge_bounds([f["bbox"] for f in group])})
        for feature in group:
            index.append({k: v for k, v in feature["properties"].items() if k != "tags"})
    for cell, group in sorted(groups.items()):
        emit(group, cell)
    index_data = write_json(destination / "index.json", {"features": sorted(index, key=lambda p: p["id"])})
    if len(index_data) > MAX_INDEX:
        issues.append("index-over-12MB")
    assets.insert(0, {"name": "index.json", "sha256": hashlib.sha256(index_data).hexdigest(),
                      "bytes": len(index_data), "kind": "index"})
    if len(assets) > MAX_ASSETS:
        issues.append("release-over-1000-assets-including-manifest")
    manifest = {
        "schemaVersion": 1, "snapshotId": snapshot_id, "source": source,
        "generatedAt": utcnow(), "counts": counts,
        "quality": {"state": "held" if issues else "passed", "issues": sorted(set(issues))},
        "assets": assets,
        "license": {"spdx": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
                    "url": "https://www.openstreetmap.org/copyright"},
        "diagnostics": diagnostics or {},
    }
    if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_SHA"):
        manifest["build"] = {"runId": os.environ["GITHUB_RUN_ID"], "commitSha": os.environ["GITHUB_SHA"],
                             "workflowRunUrl": os.environ.get("WORKFLOW_RUN_URL", "")}
    write_json(destination / "manifest.json", manifest)
    return manifest


def verify_assets(directory, manifest):
    issues = []
    for asset in manifest["assets"]:
        name = asset["name"]
        if Path(name).name != name or not re.fullmatch(r"[a-zA-Z0-9.-]+", name):
            issues.append("unsafe-asset-name")
            continue
        path = Path(directory) / name
        if not path.is_file() or path.stat().st_size != asset["bytes"] or sha256_file(path) != asset["sha256"]:
            issues.append("asset-integrity-failed:" + name)
    return issues


def authorize_publication(manifest, previous=None, auto_publish=False, approved_snapshot="", actor="", run_url="", run_id=""):
    """Exact reviewed snapshot + build run, or TWO distinct human-reviewed build runs."""
    if manifest["quality"]["state"] != "passed":
        return False, "quality-held"
    receipts = list((previous or {}).get("publication", {}).get("humanApprovals", []))
    valid = {item.get("runId"): item for item in receipts
             if isinstance(item, dict) and re.fullmatch(r"osm-\d{8}-[0-9a-f]{12}", str(item.get("snapshotId", "")))
             and re.fullmatch(r"[1-9][0-9]*", str(item.get("runId", "")))
             and item.get("approvedBy") and item.get("workflowRunUrl", "").startswith("https://github.com/")}
    exact_approval = (approved_snapshot == manifest["snapshotId"] and bool(actor)
                      and run_url.startswith("https://github.com/") and re.fullmatch(r"[1-9][0-9]*", run_id))
    if exact_approval:
        valid[run_id] = {"runId": run_id, "snapshotId": manifest["snapshotId"], "approvedBy": actor,
                                          "approvedAt": utcnow(), "workflowRunUrl": run_url}
        mode = "human"
    elif auto_publish and len(valid) >= 2:
        mode = "automatic"
    else:
        return False, "human-approval-required" if len(valid) < 2 else "auto-publish-disabled"
    manifest["publication"] = {"mode": mode, "humanApprovals": list(valid.values()),
                               "publishedAt": utcnow(), "workflowRunUrl": run_url}
    return True, mode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download")
    download.add_argument("--pbf", required=True)
    download.add_argument("--previous")
    build = sub.add_parser("build")
    build.add_argument("--pbf", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--previous")
    build.add_argument("--force", action="store_true", help="Reproduce an unchanged PBF for another manual review")
    approve = sub.add_parser("authorize")
    approve.add_argument("--out", required=True)
    approve.add_argument("--previous")
    args = parser.parse_args()
    previous = read_json(getattr(args, "previous", None))
    if args.command == "download":
        result = download_pbf(args.pbf, previous)
        print(json.dumps(result))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"unchanged={str(result['unchanged']).lower()}\n")
        return 0
    if args.command == "build":
        source = source_metadata(args.pbf)
        if not args.force and previous and previous.get("source", {}).get("sha256") == source["sha256"]:
            print(json.dumps({"unchanged": True, "snapshotId": previous["snapshotId"]}))
            return 0
        features, issues, counters = extract(args.pbf)
        manifest = build_snapshot(features, source, args.out, previous, issues, counters)
        print(json.dumps({"snapshotId": manifest["snapshotId"], "counts": manifest["counts"], "quality": manifest["quality"]}))
        return 0  # held is a reviewable outcome, NEVER publication permission.
    directory = Path(args.out)
    manifest = read_json(directory / "manifest.json")
    if problems := verify_assets(directory, manifest):
        raise ValueError(";".join(problems))
    allowed, reason = authorize_publication(
        manifest, previous, os.environ.get("AUTO_PUBLISH", "false").lower() == "true",
        os.environ.get("APPROVED_SNAPSHOT_ID", ""), os.environ.get("GITHUB_ACTOR", ""),
        os.environ.get("WORKFLOW_RUN_URL", ""), os.environ.get("CANDIDATE_RUN_ID", "") or os.environ.get("GITHUB_RUN_ID", ""))
    if allowed:
        write_json(directory / "manifest.json", manifest)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"allowed={str(allowed).lower()}\nsnapshot_id={manifest['snapshotId']}\nreason={reason}\n")
    print(json.dumps({"allowed": allowed, "reason": reason, "snapshotId": manifest["snapshotId"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
