"""检查跨表不变量并追溯报告证据。"""
from collections import Counter
from .contracts import digest
from .storage import decode


class IntegrityAudit:
    def __init__(self, store):
        self.store = store

    def run(self):
        issues = []
        observations = self.store.all("SELECT * FROM observations ORDER BY origin,external_id,revision")
        grouped = {}
        for row in observations:
            key = (row["origin"], row["external_id"])
            grouped.setdefault(key, []).append(row)
            if digest(decode(row["payload"])) != row["payload_hash"]:
                issues.append({"kind": "observation_hash", "key": key, "revision": row["revision"]})
        for key, revisions in grouped.items():
            numbers = [row["revision"] for row in revisions]
            if numbers != list(range(1, max(numbers) + 1)):
                issues.append({"kind": "revision_gap", "key": key})
            head = self.store.one("SELECT revision FROM observation_heads WHERE origin=? AND external_id=?", key)
            if head is None or head["revision"] != max(numbers):
                issues.append({"kind": "head_mismatch", "key": key})
        for row in self.store.all("SELECT * FROM decisions"):
            parent = self.store.one("SELECT 1 FROM observations WHERE origin=? AND external_id=? AND revision=?",
                                    (row["origin"], row["external_id"], row["revision"]))
            if parent is None:
                issues.append({"kind": "orphan_decision", "decision_id": row["decision_id"]})
        for row in self.store.all("SELECT * FROM batches"):
            counts = Counter(item["status"] for item in self.store.all(
                "SELECT status FROM batch_rows WHERE batch_id=?", (row["batch_id"],)))
            if counts["accepted"] != row["accepted"] or counts["duplicate"] != row["duplicate_count"] or counts["rejected"] != row["rejected"]:
                issues.append({"kind": "batch_count", "batch_id": row["batch_id"]})
            if sum(counts.values()) != row["cursor"]:
                issues.append({"kind": "batch_cursor", "batch_id": row["batch_id"]})
        for row in self.store.all("SELECT * FROM releases"):
            report = decode(row["report_json"])
            if report["query_hash"] != row["query_hash"] or report["event_head"] != row["event_head"]:
                issues.append({"kind": "release_metadata", "release_id": row["release_id"]})
            if report["story_count"] != len(report["stories"]):
                issues.append({"kind": "release_count", "release_id": row["release_id"]})
            if sum(item["observation_count"] for item in report["stories"]) != report["eligible_observations"]:
                issues.append({"kind": "eligible_reconciliation", "release_id": row["release_id"]})
        for row in self.store.all("SELECT * FROM collection_frames"):
            members = self.store.all("SELECT origin,external_id,revision FROM frame_members WHERE frame_id=?",
                                     (row["frame_id"],))
            if row["state"] == "closed" and len(members) != row["actual_count"]:
                issues.append({"kind": "frame_members", "frame_id": row["frame_id"]})
        return {"ok": not issues, "issues": issues, "event_head": self.store.head(),
                "observation_revisions": len(observations), "logical_observations": len(grouped)}

    def events(self, after=0, limit=100):
        from .errors import require
        require(type(after) is int and after >= 0, "invalid_cursor", "after must be nonnegative")
        require(type(limit) is int and 1 <= limit <= 1000, "invalid_limit", "limit must be 1 to 1000")
        rows = self.store.all("SELECT * FROM events WHERE event_no>? ORDER BY event_no LIMIT ?", (after, limit))
        return [dict(row) for row in rows]

    def summary(self):
        audit = self.run()
        kinds = {}
        for row in self.store.all("SELECT kind,COUNT(*) AS count FROM events GROUP BY kind ORDER BY kind"):
            kinds[row["kind"]] = row["count"]
        audit["event_kinds"] = kinds
        audit["source_count"] = self.store.one("SELECT COUNT(*) AS count FROM sources")["count"]
        audit["decision_count"] = self.store.one("SELECT COUNT(*) AS count FROM decisions")["count"]
        audit["release_count"] = self.store.one("SELECT COUNT(*) AS count FROM releases")["count"]
        return audit

