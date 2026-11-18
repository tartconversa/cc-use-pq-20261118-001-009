"""区域汇总、显式排除与故事级去重。"""
from collections import Counter, defaultdict
from .contracts import digest, report_query
from .errors import require


class EvidenceAnalytics:
    def __init__(self, store, observations, sources, coding, frames=None):
        self.store = store
        self.observations = observations
        self.sources = sources
        self.coding = coding
        self.frames = frames

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
            source_ids = sorted({row["source"]["source_id"] for row in members})
            story_rows.append({"region": region, "story_key": story, "topic": topic,
                               "audience": audience, "stance": stance,
                               "observation_count": len(members),
                               "source_ids": source_ids,
                               "outlets": sorted({row["source"]["outlet"] for row in members}),
                               "representative": {"origin": ordered[0]["item"]["data"]["origin"],
                                                  "external_id": ordered[0]["item"]["data"]["external_id"]}})
        by_region = defaultdict(lambda: {"stories": 0, "stances": Counter(), "topics": Counter(),
                                         "audiences": Counter(), "observations": 0,
                                         "source_stories": Counter(), "source_observations": Counter()})
        for story in story_rows:
            bucket = by_region[story["region"]]
            bucket["stories"] += 1
            bucket["stances"][story["stance"]] += 1
            bucket["topics"][story["topic"]] += 1
            bucket["audiences"][story["audience"]] += 1
            bucket["observations"] += story["observation_count"]
            for source_id in story["source_ids"]:
                bucket["source_stories"][source_id] += 1
        for (region, _story, _topic, _audience), members in groups.items():
            for member in members:
                by_region[region]["source_observations"][member["source"]["source_id"]] += 1
        regions = []
        for region, data in sorted(by_region.items()):
            denominator = data["stories"]
            sources = []
            for source_id in sorted(data["source_stories"]):
                sources.append({"source_id": source_id,
                                "outlet": self.sources.describe(source_id)["outlet"],
                                "stories": data["source_stories"][source_id],
                                "observations": data["source_observations"][source_id]})
            regions.append({"region": region, "story_count": denominator,
                            "observation_count": data["observations"],
                            "source_count": len(sources),
                            "multi_source_stories": sum(1 for story in story_rows
                                                        if story["region"] == region
                                                        and len(story["source_ids"]) > 1),
                            "sources": sources,
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

    def source_balance(self, report, *, concentration_threshold=0.75):
        """Put every source on equal footing without changing the story basis.

        The story basis in ``report`` weights a story once no matter how many
        outlets carried it.  This view instead averages each source's own stance
        distribution, so one heavily collected outlet cannot stand in for a
        region's media as a whole.  Missing evidence stays visible as a status
        and as ``None`` shares, never as a zero tendency.
        """
        spec = report["query"]
        require(isinstance(concentration_threshold, (int, float))
                and 0 < concentration_threshold <= 1,
                "invalid_threshold", "concentration_threshold must be in (0, 1]")
        story_index = {(story["region"], story["story_key"], story["topic"], story["audience"]): story
                       for story in report["stories"]}
        region_data = defaultdict(lambda: {
            "observations": 0, "excluded": Counter(),
            "sources": defaultdict(lambda: {"stories": {}, "observations": 0,
                                            "total_observations": 0, "excluded": Counter()}),
        })
        for row in self._rows(spec):
            region = row["source"]["region"]
            source_id = row["source"]["source_id"]
            data = row["item"]["data"]
            bucket = region_data[region]
            bucket["observations"] += 1
            bucket["sources"][source_id]["total_observations"] += 1
            category = self._classify(row, spec)
            if category != "eligible":
                bucket["excluded"][category] += 1
                bucket["sources"][source_id]["excluded"][category] += 1
                continue
            coding = row["state"]["coding"]
            key = (region, data["story_key"], coding["topic"], coding["audience"])
            source = bucket["sources"][source_id]
            source["observations"] += 1
            source["stories"].setdefault(key, []).append(coding["stance"])
        registered = defaultdict(list)
        for source in self.sources.list():
            if spec["regions"] and source["region"] not in spec["regions"]:
                continue
            registered[source["region"]].append(source)
        rows = []
        for region in sorted(set(region_data) | set(registered)):
            data = region_data[region]
            stories = {key: story for key, story in story_index.items() if key[0] == region}
            story_total = len(stories)
            eligible_sources = []
            for source_id, source in data["sources"].items():
                if not source["stories"]:
                    continue
                per_story = {}
                for key, stances in source["stories"].items():
                    per_story[key] = stances[0] if len(set(stances)) == 1 else "mixed"
                counts = Counter(per_story.values())
                eligible_sources.append({
                    "source_id": source_id,
                    "outlet": self.sources.describe(source_id)["outlet"],
                    "eligible_stories": len(per_story),
                    "eligible_observations": source["observations"],
                    "story_share": round(len(per_story) / story_total, 6) if story_total else None,
                    "stance_counts": dict(sorted(counts.items())),
                    "stance_shares": {stance: round(count / len(per_story), 6)
                                      for stance, count in sorted(counts.items())},
                    "excluded": dict(sorted(source["excluded"].items())),
                })
            eligible_sources.sort(key=lambda item: item["source_id"])
            balanced = {}
            if eligible_sources:
                stances = sorted({stance for item in eligible_sources for stance in item["stance_shares"]})
                balanced = {stance: round(sum(item["stance_shares"].get(stance, 0.0)
                                              for item in eligible_sources) / len(eligible_sources), 6)
                            for stance in stances}
            story_row = next((row for row in report["regions"] if row["region"] == region), None)
            story_counts = story_row["stance_counts"] if story_row else {}
            story_shares = story_row["stance_shares"] if story_row else {}
            eligible_observations = sum(item["eligible_observations"] for item in eligible_sources)
            multi = sum(1 for story in stories.values() if len(story["source_ids"]) > 1)
            participations = sum(item["eligible_stories"] for item in eligible_sources)
            largest_participation = (max((item["eligible_stories"] for item in eligible_sources), default=0)
                                     / participations) if participations else None
            largest_story_footprint = (max((item["eligible_stories"] for item in eligible_sources), default=0)
                                       / story_total) if story_total else None
            largest_observation_share = (max((item["eligible_observations"] for item in eligible_sources),
                                             default=0) / eligible_observations) if eligible_observations else None
            coverage_sources = []
            declared = False
            for source in registered.get(region, []):
                if self.frames is None:
                    state = "undeclared"
                    problems = []
                else:
                    state_data = self.frames.source_state(source["source_id"], spec["start"], spec["end"])
                    state = state_data["state"]
                    problems = state_data["problems"]
                    declared = declared or bool(state_data["frames"])
                coverage_sources.append({"source_id": source["source_id"], "outlet": source["outlet"],
                                         "active": source["active"],
                                         "state": state, "problems": problems,
                                         "observations_in_window":
                                             data["sources"].get(source["source_id"], {})
                                             .get("total_observations", 0)})
            incomplete_coverage = [item for item in coverage_sources
                                   if item["state"] in {"open", "drifted", "partial"}]
            limitations = []
            if not eligible_sources:
                limitations.append({"kind": "no_eligible_stories"})
            if data["excluded"].get("disputed", 0):
                limitations.append({"kind": "unresolved_disputes",
                                    "count": data["excluded"]["disputed"]})
            if len(eligible_sources) == 1:
                limitations.append({"kind": "single_source", "source_id": eligible_sources[0]["source_id"]})
            if (largest_participation is not None and len(eligible_sources) > 1
                    and largest_participation >= concentration_threshold):
                limitations.append({"kind": "source_concentration",
                                    "share": round(largest_participation, 6),
                                    "measure": "largest share of source-story participations"})
            if (largest_story_footprint is not None and len(eligible_sources) > 1
                    and largest_story_footprint >= concentration_threshold):
                limitations.append({"kind": "single_source_story_share",
                                    "share": round(largest_story_footprint, 6),
                                    "measure": "largest share of stories collected by one source"})
            for item in incomplete_coverage:
                limitations.append({"kind": "incomplete_coverage", "source_id": item["source_id"],
                                    "state": item["state"]})
            if not eligible_sources:
                status = "no_evidence" if not data["observations"] else "insufficient"
            elif limitations:
                status = "limited"
            else:
                status = "supported"
            require(participations >= story_total, "balance_mismatch",
                    "every eligible story must have at least one source")
            require(participations == sum(len(source["stories"])
                                          for source in data["sources"].values()),
                    "balance_mismatch", "source contributions do not reconcile")
            rows.append({
                "region": region,
                "story_basis": {"denominator": story_total, "stance_counts": dict(sorted(story_counts.items())),
                                "stance_shares": dict(sorted(story_shares.items()))},
                "source_basis": {"denominator": len(eligible_sources),
                                 "method": "equal weight per source over its eligible stories",
                                 "weighting": "each source contributes equally regardless of story count",
                                 "stance_shares": dict(sorted(balanced.items()))},
                "denominators": {"stories": story_total, "eligible_sources": len(eligible_sources),
                                 "source_story_participations": participations,
                                 "eligible_observations": eligible_observations,
                                 "observations_in_window": data["observations"]},
                "source_contribution": eligible_sources,
                "duplication": {"multi_source_stories": multi,
                                "reposted_observations": eligible_observations - story_total
                                if eligible_observations >= story_total else 0,
                                "observation_story_ratio": round(eligible_observations / story_total, 6)
                                if story_total else None,
                                "largest_source_participation_share": round(largest_participation, 6)
                                if largest_participation is not None else None,
                                "largest_source_story_share": round(largest_story_footprint, 6)
                                if largest_story_footprint is not None else None,
                                "largest_source_observation_share": round(largest_observation_share, 6)
                                if largest_observation_share is not None else None},
                "unresolved": {kind: data["excluded"].get(kind, 0)
                               for kind in ("disputed", "uncoded", "retracted")},
                "excluded": dict(sorted(data["excluded"].items())),
                "coverage": {"declared": declared, "sources": coverage_sources,
                             "incomplete": bool(incomplete_coverage)},
                "evidence_status": status,
                "limitations": limitations,
            })
        return {"query": spec, "query_hash": report["query_hash"],
                "event_head": report["event_head"],
                "concentration_threshold": concentration_threshold, "regions": rows}

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

