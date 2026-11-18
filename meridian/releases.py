"""预览令牌与不可变发布报告。"""
from .contracts import digest, report_query, text, utc_now
from .errors import MeridianError, require
from .storage import decode, encode


class ReleaseShelf:
    def __init__(self, store, analytics, frames, audit=None):
        self.store = store
        self.analytics = analytics
        self.frames = frames
        self.audit = audit

    def preview(self, query, *, minimum_stories=1, maximum_exclusion_share=0.4):
        report = self.analytics.report(query)
        gate = self.analytics.quality(report, minimum_stories=minimum_stories,
                                      maximum_exclusion_share=maximum_exclusion_share)
        coverage = self.frames.quality(report["query"])
        if not coverage["passed"]:
            gate["passed"] = False
            gate["reasons"].append("collection_coverage")
        token = digest({"report": report, "gate": gate, "coverage": coverage})
        return {"token": token, "report": report, "quality": gate, "coverage": coverage}

    def publish(self, release_id, query, token, *, minimum_stories=1, maximum_exclusion_share=0.4):
        with self.store.transaction():
            return self._publish_locked(release_id, query, token, minimum_stories=minimum_stories,
                                        maximum_exclusion_share=maximum_exclusion_share)

    def _publish_locked(self, release_id, query, token, *, minimum_stories=1, maximum_exclusion_share=0.4):
        release_id = text(release_id, "release_id")
        token = text(token, "token", 128)
        spec = report_query(query)
        existing = self.store.one("SELECT * FROM releases WHERE release_id=?", (release_id,))
        if existing:
            require(existing["preview_token"] == token and existing["query_hash"] == digest(spec),
                    "release_conflict", "release identifier already used")
            return self.describe(release_id) | {"duplicate": True}
        with self.store.transaction():
            if self.audit is not None:
                integrity = self.audit.run()
                require(integrity["ok"], "integrity_failed", "database integrity check failed",
                        issues=integrity["issues"][:10])
            current = self.preview(spec, minimum_stories=minimum_stories,
                                   maximum_exclusion_share=maximum_exclusion_share)
            require(current["token"] == token, "stale_preview", "data changed after preview")
            require(current["quality"]["passed"], "quality_failed", "report failed quality gate",
                    reasons=current["quality"]["reasons"])
            now = utc_now()
            self.store.db.execute("INSERT INTO releases(release_id,query_hash,event_head,preview_token,report_json,created_at) "
                                  "VALUES(?,?,?,?,?,?)", (release_id, digest(spec), current["report"]["event_head"],
                                                           token, encode(current["report"]), now))
            self.store.event("report_published", release_id, token, now)
        return self.describe(release_id) | {"duplicate": False}

    def describe(self, release_id):
        row = self.store.one("SELECT * FROM releases WHERE release_id=?", (release_id,))
        require(row is not None, "release_missing", "release not found")
        return {"release_id": release_id, "event_head": row["event_head"],
                "query_hash": row["query_hash"], "preview_token": row["preview_token"],
                "report": decode(row["report_json"]), "created_at": row["created_at"]}

    @staticmethod
    def _composition(report):
        regions = {}
        for story in report["stories"]:
            bucket = regions.setdefault(story["region"], {"stories": 0, "outlets": set(),
                                                          "multi_source_stories": 0})
            bucket["stories"] += 1
            bucket["outlets"].update(story["outlets"])
            if len(story["outlets"]) > 1:
                bucket["multi_source_stories"] += 1
        return {region: {"stories": bucket["stories"],
                         "outlets": sorted(bucket["outlets"]),
                         "multi_source_stories": bucket["multi_source_stories"]}
                for region, bucket in sorted(regions.items())}

    @staticmethod
    def _comparable(stories):
        # Frozen reports may predate the ``source_ids`` field; compare the
        # original story rows as they were, and report source participation
        # changes through ``source_composition`` instead of a false revision.
        return [{key: value for key, value in story.items() if key != "source_ids"}
                for story in stories]

    def compare(self, release_id):
        released = self.describe(release_id)
        query = released["report"]["query"]
        current = self.analytics.report(query)
        previous = released["report"]
        old_composition = self._composition(previous)
        new_composition = self._composition(current)
        balance = self.analytics.source_balance(current)
        return {"release_id": release_id, "frozen_head": released["event_head"],
                "current_head": current["event_head"],
                "same_result": self._comparable(previous["stories"]) == self._comparable(current["stories"])
                               and previous["excluded"] == current["excluded"],
                "story_count_delta": current["story_count"] - previous["story_count"],
                "excluded_delta": {key: current["excluded"].get(key, 0) - previous["excluded"].get(key, 0)
                                   for key in sorted(set(current["excluded"]) | set(previous["excluded"]))},
                "source_composition": {
                    "frozen": old_composition,
                    "current": new_composition,
                    "changed_regions": [region for region in sorted(set(old_composition) | set(new_composition))
                                        if old_composition.get(region) != new_composition.get(region)]},
                "source_balanced_current": balance}

