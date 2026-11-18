import unittest
from tests.common import ServiceCase
from meridian.demo import decision, query


class SourceBalanceTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.service.sources.register({"source_id": "north-c", "region": "North", "outlet": "Outlet D",
                                       "topics": ["technology", "culture"], "audiences": ["general", "students"]})

    def code_stance(self, external_id, stance, source_id="north-a"):
        self.add(external_id, source_id)
        self.service.coding.submit(decision(external_id, "alice", stance))
        self.service.coding.submit(decision(external_id, "bob", stance))

    def north_south(self):
        return query() | {"regions": []}

    def test_story_basis_and_source_basis_disagree_when_one_outlet_dominates(self):
        for external_id in ("n1", "n2", "n3", "n4"):
            self.code_stance(external_id, "positive")
        self.code_stance("n5", "negative", "north-b")
        self.accepted("s1", "south-a")

        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        story_shares = {stance: row["left_share"] for stance, row in comparison["stances"].items()}
        self.assertEqual(story_shares, {"negative": 0.2, "positive": 0.8})

        balanced = comparison["source_balanced"]["left"]
        self.assertEqual(balanced["story_basis"]["denominator"], 5)
        self.assertEqual(balanced["source_basis"]["denominator"], 2)
        self.assertEqual(balanced["source_basis"]["stance_shares"],
                         {"negative": 0.5, "positive": 0.5})
        self.assertEqual(comparison["source_balanced"]["stances"]["positive"]["left_basis_delta"], -0.3)
        self.assertEqual(comparison["source_balanced"]["stances"]["positive"]["left_share"], 0.5)
        self.assertEqual(comparison["source_balanced"]["stances"]["positive"]["story_left_share"], 0.8)
        contributions = {row["source_id"]: row for row in balanced["source_contribution"]}
        self.assertEqual(contributions["north-a"]["eligible_stories"], 4)
        self.assertEqual(contributions["north-a"]["story_share"], 0.8)
        self.assertEqual(contributions["north-b"]["eligible_stories"], 1)
        self.assertEqual(comparison["evidence"]["left"]["evidence_status"], "limited")
        self.assertIn("source_concentration", [item["kind"] for item in balanced["limitations"]])
        self.assertEqual(comparison["evidence"]["right"]["evidence_status"], "limited")
        self.assertIn("single_source", [item["kind"] for item in
                                        comparison["source_balanced"]["right"]["limitations"]])

    def test_reposts_count_once_as_story_but_all_sources_participate(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared")
        self.accepted("c", "north-c", "shared")
        self.accepted("s", "south-a", "south-story")
        report = self.service.analytics.report(query())
        north = next(row for row in report["regions"] if row["region"] == "North")
        self.assertEqual(north["story_count"], 1)
        self.assertEqual(north["observation_count"], 3)
        self.assertEqual(north["source_count"], 3)
        self.assertEqual(north["multi_source_stories"], 1)
        self.assertEqual(sorted(row["source_id"] for row in north["sources"]),
                         ["north-a", "north-b", "north-c"])
        balance = self.service.analytics.source_balance(report)
        north_balance = next(row for row in balance["regions"] if row["region"] == "North")
        self.assertEqual(north_balance["source_basis"]["denominator"], 3)
        self.assertEqual(north_balance["duplication"]["multi_source_stories"], 1)
        self.assertEqual(north_balance["duplication"]["reposted_observations"], 2)
        self.assertEqual(north_balance["denominators"]["stories"], 1)
        self.assertEqual(north_balance["denominators"]["eligible_observations"], 3)

    def test_all_disputed_region_is_insufficient_not_zero(self):
        self.accepted("n1", "north-a")
        self.add("s1", "south-a")
        self.service.coding.submit(decision("s1", "alice", "positive"))
        self.service.coding.submit(decision("s1", "bob", "negative"))
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        south = comparison["source_balanced"]["right"]
        self.assertEqual(south["evidence_status"], "insufficient")
        self.assertEqual(south["story_basis"]["denominator"], 0)
        self.assertEqual(south["source_basis"]["denominator"], 0)
        self.assertEqual(south["story_basis"]["stance_shares"], {})
        self.assertEqual(south["unresolved"]["disputed"], 1)
        kinds = [item["kind"] for item in south["limitations"]]
        self.assertIn("no_eligible_stories", kinds)
        self.assertIn("unresolved_disputes", kinds)
        self.assertIsNone(comparison["source_balanced"]["stances"]["positive"]["right_share"])
        self.assertIsNone(comparison["source_balanced"]["stances"]["positive"]["difference"])

    def test_open_frame_marks_source_and_region_limited(self):
        self.accepted("a", "north-a")
        self.accepted("b", "north-b")
        self.accepted("s", "south-a")
        self.service.frames.open("north-b-frame", "north-b", query()["start"], query()["end"], 1)
        self.service.frames.open("south-frame", "south-a", query()["start"], query()["end"], 1)
        self.service.frames.open("north-a-frame", "north-a", query()["start"], query()["end"], 1)
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        north = comparison["source_balanced"]["left"]
        coverage = {row["source_id"]: row["state"] for row in north["coverage"]["sources"]}
        self.assertEqual(coverage["north-a"], "open")
        self.assertEqual(coverage["north-b"], "open")
        self.assertTrue(north["coverage"]["incomplete"])
        self.assertIn("incomplete_coverage", [item["kind"] for item in north["limitations"]])
        self.assertNotEqual(north["evidence_status"], "supported")

    def test_closed_then_drifted_frame_is_flagged(self):
        self.accepted("a", "north-a")
        self.accepted("b", "north-b")
        self.accepted("s", "south-a")
        self.service.frames.open("north-a-frame", "north-a", query()["start"], query()["end"], 1)
        self.service.frames.close("north-a-frame")
        self.service.frames.open("north-b-frame", "north-b", query()["start"], query()["end"], 1)
        self.service.frames.close("north-b-frame")
        self.service.frames.open("south-frame", "south-a", query()["start"], query()["end"], 1)
        self.service.frames.close("south-frame")
        self.accepted("b2", "north-b")
        # second observation changes the frame member set after sealing
        self.service.frames.describe("north-b-frame")
        changed = self.service.observations.describe("local-fixture", "b")["data"] | {"story_key": "b"}
        self.service.observations.ingest(changed, expected_revision=1)
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        north = comparison["source_balanced"]["left"]
        coverage = {row["source_id"]: row["state"] for row in north["coverage"]["sources"]}
        self.assertEqual(coverage["north-b"], "drifted")
        self.assertIn("incomplete_coverage", [item["kind"] for item in north["limitations"]])

    def test_partial_frame_coverage_is_marked(self):
        self.accepted("a", "north-a")
        self.accepted("s", "south-a")
        self.service.frames.open("a-early", "north-a", query()["start"], "2026-11-10T00:00:00Z", 1)
        self.service.frames.close("a-early")
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        north = comparison["source_balanced"]["left"]
        coverage = {row["source_id"]: row["state"] for row in north["coverage"]["sources"]}
        self.assertEqual(coverage["north-a"], "partial")
        self.assertIn("incomplete_coverage", [item["kind"] for item in north["limitations"]])

    def test_unframed_sources_are_not_treated_as_complete(self):
        self.accepted("a", "north-a")
        self.accepted("s", "south-a")
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        north = comparison["source_balanced"]["left"]
        self.assertFalse(north["coverage"]["declared"])
        self.assertEqual({row["state"] for row in north["coverage"]["sources"]}, {"unframed"})
        self.assertFalse(north["coverage"]["incomplete"])
        self.assertEqual(north["evidence_status"], "limited")
        self.assertEqual([item["kind"] for item in north["limitations"]], ["single_source"])

    def test_fully_covered_region_is_supported(self):
        self.code_stance("a", "positive")
        self.code_stance("b", "positive", "north-b")
        self.accepted("s", "south-a")
        self.accepted("s2", "south-a")
        for source_id, expected in (("north-a", 1), ("north-b", 1)):
            self.service.frames.open(f"{source_id}-frame", source_id, query()["start"], query()["end"], expected)
            self.service.frames.close(f"{source_id}-frame")
        self.service.frames.open("south-frame", "south-a", query()["start"], query()["end"], 2)
        self.service.frames.close("south-frame")
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        self.assertEqual(comparison["evidence"]["left"]["evidence_status"], "supported")
        self.assertEqual(comparison["evidence"]["right"]["evidence_status"], "limited")
        self.assertEqual(comparison["source_balanced"]["left"]["source_basis"]["denominator"], 2)

    def test_frozen_release_keeps_original_data_while_comparison_tracks_change(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared")
        preview = self.service.releases.preview(query())
        release = self.service.releases.publish("release", query(), preview["token"])
        frozen_before = self.service.exports.frozen("release")["bundle_hash"]
        changed = self.service.observations.describe("local-fixture", "a")["data"] | {"story_key": "moved"}
        self.service.observations.ingest(changed, expected_revision=1)
        self.assertEqual(self.service.releases.describe("release")["report"], release["report"])
        self.assertEqual(frozen_before, self.service.exports.frozen("release")["bundle_hash"])
        updated = self.service.releases.compare("release")
        self.assertIn("source_composition", updated)
        self.assertEqual(updated["source_composition"]["changed_regions"], ["North"])
        self.assertTrue(updated["source_balanced_current"]["regions"])

    def test_new_dispute_appears_in_comparison_but_not_frozen_release(self):
        self.accepted("a", "north-a")
        self.accepted("s", "south-a")
        preview = self.service.releases.preview(query())
        release = self.service.releases.publish("release", query(), preview["token"])
        self.add("b", "north-a")
        self.service.coding.submit(decision("b", "alice", "positive"))
        self.service.coding.submit(decision("b", "bob", "negative"))
        comparison = self.service.comparisons.regions(self.north_south(), "North", "South")
        north = comparison["source_balanced"]["left"]
        self.assertEqual(north["unresolved"]["disputed"], 1)
        self.assertIn("unresolved_disputes", [item["kind"] for item in north["limitations"]])
        self.assertEqual(self.service.releases.describe("release")["report"], release["report"])
        updated = self.service.releases.compare("release")
        self.assertEqual(updated["excluded_delta"], {"disputed": 1})
        # a disputed observation is excluded from eligible stories, so the
        # story/source composition itself has not moved yet
        self.assertEqual(updated["source_composition"]["changed_regions"], [])

    def test_source_breakdown_exposes_balanced_view(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared")
        result = self.service.exports.source_breakdown(query())
        contributions = {row["source_id"]: row for row in result["sources"]}
        self.assertEqual(contributions["north-a"]["eligible_stories"], 1)
        self.assertEqual(contributions["north-b"]["eligible_stories"], 1)
        balanced = {row["region"]: row for row in result["source_balanced"]["regions"]}
        self.assertEqual(balanced["North"]["duplication"]["reposted_observations"], 1)
        self.assertEqual(balanced["North"]["source_basis"]["denominator"], 2)


if __name__ == "__main__":
    unittest.main()
