"""解释观察记录未进入比较结果的原因。"""
from collections import Counter, defaultdict
from .contracts import report_query
from .errors import require


class ReviewDiagnostics:
    def __init__(self, store, observations, sources, coding, analytics, frames):
        self.store = store
        self.observations = observations
        self.sources = sources
        self.coding = coding
        self.analytics = analytics
        self.frames = frames

    def queue(self, query, *, include_retracted=False):
        spec = report_query(query)
        pending = []
        for row in self.analytics._rows(spec):
            item = row["item"]
            data = item["data"]
            state = row["state"]
            category = self.analytics._classify(row, spec)
            if category not in {"uncoded", "disputed", "retracted"}:
                continue
            if category == "retracted" and not include_retracted:
                continue
            pending.append({"origin": data["origin"], "external_id": data["external_id"],
                            "revision": item["revision"], "source_id": data["source_id"],
                            "region": row["source"]["region"], "published_at": data["published_at"],
                            "status": category, "reviewers": [decision["reviewer"] for decision in state["decisions"]],
                            "story_key": data["story_key"]})
        order = {"disputed": 0, "uncoded": 1, "retracted": 2}
        pending.sort(key=lambda row: (order[row["status"]], row["published_at"],
                                      row["origin"], row["external_id"]))
        totals = Counter(row["status"] for row in pending)
        return {"query": spec, "total": len(pending), "by_status": dict(sorted(totals.items())),
                "items": pending}

    def observation_explanation(self, origin, external_id, query):
        spec = report_query(query)
        item = self.observations.describe(origin, external_id)
        data = item["data"]
        source = self.sources.describe(data["source_id"])
        reasons = []
        if not spec["start"] <= data["published_at"] < spec["end"]:
            reasons.append("outside_window")
        if spec["regions"] and source["region"] not in spec["regions"]:
            reasons.append("outside_region")
        state = self.coding.status(origin, external_id, item["revision"])
        category = None
        if not reasons:
            category = self.analytics._classify({"item": item, "source": source, "state": state}, spec)
            if category != "eligible":
                reasons.append(category)
        report = self.analytics.report(spec)
        matches = [story for story in report["stories"] if story["region"] == source["region"]
                   and story["story_key"] == data["story_key"]]
        if category == "eligible" and not matches:
            reasons.append("aggregation_gap")
        return {"origin": origin, "external_id": external_id, "revision": item["revision"],
                "source": source, "coding_status": state["status"],
                "eligible": not reasons, "reasons": reasons, "story_rows": matches,
                "query_hash": report["query_hash"], "event_head": report["event_head"]}

    def story_conflicts(self, query):
        spec = report_query(query)
        rows = self.analytics._rows(spec)
        grouped = defaultdict(list)
        for row in rows:
            if self.analytics._classify(row, spec) != "eligible":
                continue
            data = row["item"]["data"]
            coding = row["state"]["coding"]
            key = (row["source"]["region"], data["story_key"], coding["topic"], coding["audience"])
            grouped[key].append({"origin": data["origin"], "external_id": data["external_id"],
                                 "source_id": data["source_id"], "stance": coding["stance"]})
        conflicts = []
        for key, items in sorted(grouped.items()):
            if len({item["stance"] for item in items}) > 1:
                conflicts.append({"region": key[0], "story_key": key[1], "topic": key[2],
                                  "audience": key[3], "observations": items})
        return {"query": spec, "count": len(conflicts), "conflicts": conflicts}

    def readiness(self, query, *, minimum_stories=1, maximum_exclusion_share=0.4):
        report = self.analytics.report(query)
        quality = self.analytics.quality(report, minimum_stories=minimum_stories,
                                         maximum_exclusion_share=maximum_exclusion_share)
        coverage = self.frames.quality(report["query"])
        queue = self.queue(query)
        conflicts = self.story_conflicts(query)
        warnings = []
        if not coverage["passed"]:
            warnings.append("collection_coverage")
        if conflicts["count"]:
            warnings.append("story_coding_conflicts")
        if queue["by_status"].get("disputed", 0):
            warnings.append("unresolved_decisions")
        return {"query_hash": report["query_hash"], "event_head": report["event_head"],
                "quality": quality, "coverage": coverage, "queue": queue["by_status"],
                "story_conflicts": conflicts["count"], "warnings": warnings,
                "ready": quality["passed"] and not warnings}

