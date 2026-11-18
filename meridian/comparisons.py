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

    def regions(self, query, left_region, right_region):
        spec = report_query(query)
        require(left_region != right_region, "same_region", "choose distinct regions")
        if spec["regions"]:
            require(left_region in spec["regions"] and right_region in spec["regions"],
                    "incompatible_query", "both regions must belong to query")
        report = self.analytics.report(spec)
        regions = self._region_map(report)
        require(left_region in regions and right_region in regions, "region_missing",
                "both regions need eligible stories")
        left = regions[left_region]
        right = regions[right_region]
        stances = {}
        for stance in sorted(set(left["stance_counts"]) | set(right["stance_counts"])):
            left_share = left["stance_counts"].get(stance, 0) / left["story_count"]
            right_share = right["stance_counts"].get(stance, 0) / right["story_count"]
            stances[stance] = {"left_share": round(left_share, 6),
                                "right_share": round(right_share, 6),
                                "difference": round(right_share - left_share, 6)}
        return {"query_hash": report["query_hash"], "event_head": report["event_head"],
                "left": left, "right": right, "stances": stances, "excluded": report["excluded"]}

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

