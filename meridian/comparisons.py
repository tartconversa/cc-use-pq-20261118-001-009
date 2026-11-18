"""保留分母的窗口与地区对比。"""
from collections import defaultdict
from .contracts import report_query
from .errors import require


class ComparisonEngine:
    def __init__(self, analytics):
        self.analytics = analytics

    @staticmethod
    def _region_map(report):
        return {row["region"]: row for row in report["regions"]}

    def windows(self, left_query, right_query):
        left = self.analytics.report(left_query)
        right = self.analytics.report(right_query)
        require(left["query"]["regions"] == right["query"]["regions"], "incompatible_query",
                "region filters must match for comparison")
        require(left["query"]["topics"] == right["query"]["topics"], "incompatible_query",
                "topic filters must match for comparison")
        require(left["query"]["audiences"] == right["query"]["audiences"], "incompatible_query",
                "audience filters must match for comparison")
        left_regions = self._region_map(left)
        right_regions = self._region_map(right)
        rows = []
        for region in sorted(set(left_regions) | set(right_regions)):
            previous = left_regions.get(region, {})
            current = right_regions.get(region, {})
            old_count = previous.get("story_count", 0)
            new_count = current.get("story_count", 0)
            stances = {}
            for stance in sorted(set(previous.get("stance_counts", {})) | set(current.get("stance_counts", {}))):
                old = previous.get("stance_counts", {}).get(stance, 0)
                new = current.get("stance_counts", {}).get(stance, 0)
                old_share = old / old_count if old_count else None
                new_share = new / new_count if new_count else None
                stances[stance] = {"left_count": old, "right_count": new, "count_delta": new - old,
                                    "left_share": round(old_share, 6) if old_share is not None else None,
                                    "right_share": round(new_share, 6) if new_share is not None else None,
                                    "share_delta": round(new_share - old_share, 6)
                                    if old_share is not None and new_share is not None else None}
            rows.append({"region": region, "left_stories": old_count, "right_stories": new_count,
                         "story_delta": new_count - old_count, "stances": stances})
        return {"left_query_hash": left["query_hash"], "right_query_hash": right["query_hash"],
                "left_head": left["event_head"], "right_head": right["event_head"],
                "left_excluded": left["excluded"], "right_excluded": right["excluded"],
                "regions": rows}

    def regions(self, query, left_region, right_region, *, concentration_threshold=0.75):
        spec = report_query(query)
        require(left_region != right_region, "same_region", "choose distinct regions")
        if spec["regions"]:
            require(left_region in spec["regions"] and right_region in spec["regions"],
                    "incompatible_query", "both regions must belong to query")
        report = self.analytics.report(spec)
        balance = self.analytics.source_balance(report, concentration_threshold=concentration_threshold)
        regions = self._region_map(report)
        balanced = {row["region"]: row for row in balance["regions"]}
        for name in (left_region, right_region):
            require(name in regions or (name in balanced
                                        and balanced[name]["denominators"]["observations_in_window"]),
                    "region_missing", "both regions need evidence in the window")
        left = regions.get(left_region)
        right = regions.get(right_region)
        left_counts = left["stance_counts"] if left else {}
        right_counts = right["stance_counts"] if right else {}
        left_total = left["story_count"] if left else 0
        right_total = right["story_count"] if right else 0
        left_balanced = balanced[left_region]
        right_balanced = balanced[right_region]
        stances = {}
        for stance in sorted(set(left_counts) | set(right_counts)
                             | set(left_balanced["story_basis"]["stance_counts"])
                             | set(right_balanced["story_basis"]["stance_counts"])):
            left_share = left_counts.get(stance, 0) / left_total if left_total else None
            right_share = right_counts.get(stance, 0) / right_total if right_total else None
            stances[stance] = {"left_share": round(left_share, 6) if left_share is not None else None,
                                "right_share": round(right_share, 6) if right_share is not None else None,
                                "difference": round(right_share - left_share, 6)
                                if left_share is not None and right_share is not None else None}
        source_stances = {}
        for stance in sorted(set(left_balanced["source_basis"]["stance_shares"])
                             | set(right_balanced["source_basis"]["stance_shares"])):
            left_share = left_balanced["source_basis"]["stance_shares"].get(stance)
            right_share = right_balanced["source_basis"]["stance_shares"].get(stance)
            source_stances[stance] = {
                "left_share": left_share, "right_share": right_share,
                "difference": round(right_share - left_share, 6)
                if left_share is not None and right_share is not None else None,
                "story_left_share": stances.get(stance, {}).get("left_share"),
                "story_right_share": stances.get(stance, {}).get("right_share"),
                "left_basis_delta": round(left_share - stances[stance]["left_share"], 6)
                if left_share is not None and stance in stances
                and stances[stance]["left_share"] is not None else None,
                "right_basis_delta": round(right_share - stances[stance]["right_share"], 6)
                if right_share is not None and stance in stances
                and stances[stance]["right_share"] is not None else None,
            }
        def evidence(row):
            return {"evidence_status": row["evidence_status"],
                    "denominators": row["denominators"],
                    "source_count": row["source_basis"]["denominator"],
                    "unresolved": row["unresolved"],
                    "duplication": row["duplication"],
                    "coverage": row["coverage"],
                    "limitations": row["limitations"]}

        return {"query_hash": report["query_hash"], "event_head": report["event_head"],
                "left": left, "right": right, "stances": stances,
                "source_balanced": {"left": left_balanced, "right": right_balanced,
                                    "stances": source_stances,
                                    "concentration_threshold": concentration_threshold},
                "excluded": report["excluded"],
                "evidence": {"left": evidence(left_balanced), "right": evidence(right_balanced)},
                "caveat": "Story-basis shares weight each story once; source-balanced shares "
                          "average per-source shares. A limited or insufficient status is missing "
                          "evidence, not a zero tendency.",
                "basis": "story and source-balanced"}

    def topic_matrix(self, query):
        report = self.analytics.report(query)
        matrix = defaultdict(lambda: defaultdict(lambda: {"stories": 0, "stances": defaultdict(int)}))
        for story in report["stories"]:
            cell = matrix[story["region"]][story["topic"]]
            cell["stories"] += 1
            cell["stances"][story["stance"]] += 1
        rows = []
        for region, topics in sorted(matrix.items()):
            for topic, cell in sorted(topics.items()):
                rows.append({"region": region, "topic": topic, "story_count": cell["stories"],
                             "stance_counts": dict(sorted(cell["stances"].items()))})
        require(sum(row["story_count"] for row in rows) == report["story_count"], "matrix_mismatch",
                "topic matrix does not reconcile to report")
        return {"query_hash": report["query_hash"], "event_head": report["event_head"],
                "total_stories": report["story_count"], "rows": rows}

