import unittest
from tests.common import ServiceCase
from meridian.demo import decision, query
from meridian.errors import MeridianError


class ReportTests(ServiceCase):
    def test_same_story_across_outlets_has_one_denominator(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared")
        report = self.report()
        self.assertEqual(report["eligible_observations"], 2)
        self.assertEqual(report["story_count"], 1)
        self.assertEqual(report["stories"][0]["observation_count"], 2)
        self.assertEqual(report["regions"][0]["stance_shares"], {"positive": 1.0})

    def test_story_disagreement_blocks_release(self):
        self.accepted("a", "north-a", "shared")
        self.add("b", "north-b", "shared")
        self.service.coding.submit(decision("b", "alice", "negative"))
        self.service.coding.submit(decision("b", "bob", "negative"))
        preview = self.service.releases.preview(query())
        self.assertEqual(preview["report"]["stories"][0]["stance"], "mixed")
        self.assertIn("cross_source_stance_conflict", preview["quality"]["reasons"])
        with self.assertRaises(MeridianError) as caught:
            self.service.releases.publish("release", query(), preview["token"])
        self.assertEqual(caught.exception.code, "quality_failed")

    def test_released_report_remains_frozen_after_retraction(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        release = self.service.releases.publish("release", query(), preview["token"])
        self.service.observations.retract("local-fixture", "a", "withdrawn", expected_revision=1)
        self.assertEqual(release["report"]["story_count"], 1)
        self.assertEqual(self.service.releases.describe("release")["report"]["story_count"], 1)
        self.assertEqual(self.service.releases.compare("release")["story_count_delta"], -1)
        self.assertEqual(self.service.provenance.release_changes("release")["changed_stories"][0]["change"], "removed")

    def test_stale_preview_is_rejected(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        self.accepted("b")
        with self.assertRaises(MeridianError) as caught:
            self.service.releases.publish("release", query(), preview["token"])
        self.assertEqual(caught.exception.code, "stale_preview")

    def test_release_retry_is_idempotent(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        first = self.service.releases.publish("release", query(), preview["token"])
        head = self.service.store.head()
        second = self.service.releases.publish("release", query(), preview["token"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(head, self.service.store.head())

    def test_exclusion_share_blocks_release(self):
        self.accepted("a")
        self.add("b")
        preview = self.service.releases.preview(query(), maximum_exclusion_share=0.2)
        self.assertFalse(preview["quality"]["passed"])
        self.assertIn("excess_exclusions", preview["quality"]["reasons"])

    def test_topic_filter_uses_coding_not_headline(self):
        self.accepted("a")
        spec = query() | {"topics": ["culture"]}
        report = self.service.analytics.report(spec)
        self.assertEqual(report["story_count"], 0)
        self.assertEqual(report["excluded"], {"outside_topic": 1})

    def test_half_open_window_excludes_end(self):
        self.accepted("a")
        spec = query() | {"end": "2026-11-01T10:00:00Z"}
        self.assertEqual(self.service.analytics.report(spec)["observations_in_window"], 0)

    def test_compare_regions_requires_nonempty_regions(self):
        self.accepted("a")
        with self.assertRaises(MeridianError) as caught:
            self.service.comparisons.regions(query() | {"regions": []}, "North", "South")
        self.assertEqual(caught.exception.code, "region_missing")

    def test_topic_matrix_reconciles_total(self):
        self.accepted("a")
        self.accepted("b")
        matrix = self.service.comparisons.topic_matrix(query())
        self.assertEqual(matrix["total_stories"], 2)
        self.assertEqual(matrix["rows"][0]["story_count"], 2)


if __name__ == "__main__":
    unittest.main()
