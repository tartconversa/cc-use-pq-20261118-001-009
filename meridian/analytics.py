"""区域汇总、显式排除与故事级去重。"""
from collections import Counter, defaultdict
from .contracts import digest, report_query
from .errors import require


class EvidenceAnalytics:
    def __init__(self, store, observations, sources, coding):
        self.store = store
        self.observations = observations
        self.sources = sources
        self.coding = coding

    def _rows(self, spec):
        rows = []
        for item in self.observations.current():
            data = item["data"]
            if not spec["start"] <= data["published_at"] < spec["end"]:
                continue
            source = self.sources.describe(data["source_id"])
            if spec["regions"] and source["region"] not in spec["regions"]:
                continue
            state = self.coding.status(data["origin"], data["external_id"], item["revision"])
            rows.append({"item": item, "source": source, "state": state})
        return rows

    def _classify(self, row, spec):
        item = row["item"]
        state = row["state"]
        if item["status"] == "retracted":
            return "retracted"
        if state["status"] == "awaiting_review":
            return "uncoded"
        if state["status"] == "disputed":
            return "disputed"
        coding = state["coding"]
        if spec["topics"] and coding["topic"] not in spec["topics"]:
            return "outside_topic"
        if spec["audiences"] and coding["audience"] not in spec["audiences"]:
            return "outside_audience"
        return "eligible"

    def report(self, payload):
        spec = report_query(payload)
        observed = self._rows(spec)
        exclusions = Counter()
        groups = defaultdict(list)
        provenance = []
        for row in observed:
            category = self._classify(row, spec)
            data = row["item"]["data"]
            provenance.append({"origin": data["origin"], "external_id": data["external_id"],
                               "revision": row["item"]["revision"],
                               "source_id": data["source_id"], "category": category})
            if category != "eligible":
                exclusions[category] += 1
                continue
            item = row["item"]["data"]
            coding = row["state"]["coding"]
            key = (row["source"]["region"], item["story_key"], coding["topic"], coding["audience"])
            groups[key].append(row)
        story_rows = []
        for (region, story, topic, audience), members in sorted(groups.items()):
            ordered = sorted(members, key=lambda row: (row["item"]["data"]["published_at"],
                                                        row["item"]["data"]["source_id"],
                                                        row["item"]["data"]["origin"],
                                                        row["item"]["data"]["external_id"]))
            stances = [row["state"]["coding"]["stance"] for row in members]
            stance = stances[0] if len(set(stances)) == 1 else "mixed"
            story_rows.append({"region": region, "story_key": story, "topic": topic,
                               "audience": audience, "stance": stance,
                               "observation_count": len(members),
                               "outlets": sorted({row["source"]["outlet"] for row in members}),
                               "representative": {"origin": ordered[0]["item"]["data"]["origin"],
                                                  "external_id": ordered[0]["item"]["data"]["external_id"]}})
        by_region = defaultdict(lambda: {"stories": 0, "stances": Counter(), "topics": Counter(),
                                         "audiences": Counter(), "observations": 0})
        for story in story_rows:
            bucket = by_region[story["region"]]
            bucket["stories"] += 1
            bucket["stances"][story["stance"]] += 1
            bucket["topics"][story["topic"]] += 1
            bucket["audiences"][story["audience"]] += 1
            bucket["observations"] += story["observation_count"]
        regions = []
        for region, data in sorted(by_region.items()):
            denominator = data["stories"]
            regions.append({"region": region, "story_count": denominator,
                            "observation_count": data["observations"],
                            "stance_counts": dict(sorted(data["stances"].items())),
                            "stance_shares": {key: round(value / denominator, 6)
                                              for key, value in sorted(data["stances"].items())},
                            "topic_counts": dict(sorted(data["topics"].items())),
                            "audience_counts": dict(sorted(data["audiences"].items()))})
        return {"query": spec, "query_hash": digest(spec), "event_head": self.store.head(),
                "observations_in_window": len(observed), "eligible_observations": sum(len(v) for v in groups.values()),
                "excluded": dict(sorted(exclusions.items())), "story_count": len(story_rows),
                "stories": story_rows, "regions": regions,
                "provenance": sorted(provenance, key=lambda item: (item["origin"], item["external_id"]))}

    def trace_story(self, spec, region, story_key):
        report = self.report(spec)
        rows = [row for row in report["stories"] if row["region"] == region and row["story_key"] == story_key]
        require(rows, "story_missing", "story not found in query")
        observations = []
        for row in self._rows(report["query"]):
            data = row["item"]["data"]
            if row["source"]["region"] == region and data["story_key"] == story_key:
                observations.append({"origin": data["origin"], "external_id": data["external_id"],
                                     "revision": row["item"]["revision"],
                                     "classification": self._classify(row, report["query"]),
                                     "coding_status": row["state"]["status"],
                                     "source_id": data["source_id"]})
        return {"stories": rows, "observations": sorted(observations, key=lambda x: (x["origin"], x["external_id"]))}

    def quality(self, report, *, minimum_stories=1, maximum_exclusion_share=0.4):
        require(type(minimum_stories) is int and minimum_stories >= 1, "invalid_gate", "minimum_stories invalid")
        require(isinstance(maximum_exclusion_share, (int, float)) and 0 <= maximum_exclusion_share <= 1,
                "invalid_gate", "maximum_exclusion_share invalid")
        total = report["observations_in_window"]
        reasons = []
        if report["story_count"] < minimum_stories:
            reasons.append("insufficient_stories")
        excluded = sum(report["excluded"].values())
        if total == 0 or excluded / total > maximum_exclusion_share:
            reasons.append("excess_exclusions")
        if report["excluded"].get("disputed", 0):
            reasons.append("unresolved_disputes")
        if any(story["stance"] == "mixed" and story["observation_count"] > 1
               for story in report["stories"]):
            reasons.append("cross_source_stance_conflict")
        return {"passed": not reasons, "reasons": reasons, "total": total,
                "excluded": excluded, "minimum_stories": minimum_stories,
                "maximum_exclusion_share": maximum_exclusion_share}

