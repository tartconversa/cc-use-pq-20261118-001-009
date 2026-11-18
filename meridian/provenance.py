"""证据血缘和冻结视图与当前视图的差异解释。"""
from collections import Counter, defaultdict
from .contracts import report_query
from .errors import require


class ProvenanceExplorer:
    def __init__(self, store, observations, coding, analytics, releases):
        self.store = store
        self.observations = observations
        self.coding = coding
        self.analytics = analytics
        self.releases = releases

    @staticmethod
    def _story_index(report):
        return {(item["region"], item["story_key"], item["topic"], item["audience"]): item
                for item in report["stories"]}

    @staticmethod
    def _story_key(key):
        return {"region": key[0], "story_key": key[1], "topic": key[2], "audience": key[3]}

    def release_changes(self, release_id):
        old = self.releases.describe(release_id)
        current = self.analytics.report(old["report"]["query"])
        before = self._story_index(old["report"])
        after = self._story_index(current)
        changes = []
        for key in sorted(set(before) | set(after)):
            left = before.get(key)
            right = after.get(key)
            if left == right:
                continue
            changes.append({"key": self._story_key(key),
                            "change": "added" if left is None else "removed" if right is None else "revised",
                            "before": left, "after": right})
        excluded_before = old["report"]["excluded"]
        excluded_after = current["excluded"]
        exclusion_changes = {kind: {"before": excluded_before.get(kind, 0),
                                    "after": excluded_after.get(kind, 0)}
                             for kind in sorted(set(excluded_before) | set(excluded_after))
                             if excluded_before.get(kind, 0) != excluded_after.get(kind, 0)}
        return {"release_id": release_id, "frozen_head": old["event_head"],
                "current_head": current["event_head"], "changed_stories": changes,
                "exclusion_changes": exclusion_changes,
                "same_result": not changes and not exclusion_changes}

    def observation_history(self, origin, external_id):
        revisions = self.observations.lineage(origin, external_id)
        result = []
        for item in revisions:
            decisions = self.coding.decisions_for(origin, external_id, item["revision"])
            state = self.coding.status(origin, external_id, item["revision"])
            events = self.store.all("SELECT event_no,kind,occurred_at FROM events WHERE entity_key=? ORDER BY event_no",
                                    (f"{origin}/{external_id}",))
            result.append({"revision": item["revision"], "is_head": item["is_head"],
                           "status": item["status"], "hash": item["hash"],
                           "supersedes": item["supersedes"], "data": item["data"],
                           "decision_ids": [row["decision_id"] for row in decisions],
                           "coding_status": state["status"],
                           "events": [dict(row) for row in events]})
        return {"origin": origin, "external_id": external_id, "revisions": result}

    def source_contribution(self, query, source_id):
        spec = report_query(query)
        rows = [row for row in self.analytics._rows(spec) if row["source"]["source_id"] == source_id]
        require(rows, "source_outside_query", "source has no observations in query")
        categories = Counter(self.analytics._classify(row, spec) for row in rows)
        stories = defaultdict(list)
        for row in rows:
            if self.analytics._classify(row, spec) != "eligible":
                continue
            data = row["item"]["data"]
            coding = row["state"]["coding"]
            key = (row["source"]["region"], data["story_key"], coding["topic"], coding["audience"])
            stories[key].append(data["external_id"])
        report = self.analytics.report(spec)
        index = self._story_index(report)
        story_items = []
        for key, ids in sorted(stories.items()):
            combined = index.get(key)
            story_items.append({"key": self._story_key(key), "source_external_ids": sorted(ids),
                                "combined_observation_count": combined["observation_count"] if combined else None,
                                "combined_stance": combined["stance"] if combined else None})
        return {"source_id": source_id, "query_hash": report["query_hash"],
                "event_head": report["event_head"], "observation_count": len(rows),
                "categories": dict(sorted(categories.items())), "stories": story_items}

    def coding_lineage(self, origin, external_id):
        item = self.observations.describe(origin, external_id)
        decisions = self.coding.decisions_for(origin, external_id, item["revision"])
        status = self.coding.status(origin, external_id, item["revision"])
        key = f"{origin}/{external_id}/{item['revision']}"
        ruling = self.store.one("SELECT * FROM adjudications WHERE case_key=?", (key,))
        return {"origin": origin, "external_id": external_id, "revision": item["revision"],
                "observation_hash": item["hash"], "status": status["status"],
                "decisions": decisions, "adjudication":
                {field: ruling[field] for field in ("adjudicator", "topic", "stance", "audience",
                                                   "reason", "event_no")} if ruling else None}

