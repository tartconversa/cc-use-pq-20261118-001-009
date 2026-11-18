import unittest
from tests.common import ServiceCase
from meridian.demo import decision, observation, query, source
from meridian.errors import MeridianError
from meridian.service import MeridianService


class RecoveryAndContractTests(ServiceCase):
    def test_reopen_preserves_release_and_batch_progress(self):
        rows = [observation("a", "north-a", "a"), observation("b", "north-a", "b")]
        first = self.service.batches.step("batch", rows, limit=1)
        self.assertEqual(first["cursor"], 1)
        self.code("a")
        preview = self.service.releases.preview(query())
        self.service.releases.publish("release", query(), preview["token"])
        self.service.close()
        self.service = MeridianService(self.path)
        resumed = self.service.batches.step("batch", rows)
        self.assertEqual(resumed["accepted"], 2)
        self.assertEqual(self.service.releases.describe("release")["report"]["story_count"], 1)
        self.assertEqual(self.service.releases.compare("release")["story_count_delta"], 0)
        self.assertTrue(self.service.audit.run()["ok"])

    def test_timezone_normalization_makes_duplicate_stable(self):
        first = observation("a", "north-a", "a")
        self.service.observations.ingest(first)
        equivalent = first | {"published_at": "2026-11-01T18:00:00+08:00"}
        repeat = self.service.observations.ingest(equivalent)
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(len(self.service.observations.lineage("local-fixture", "a")), 1)

    def test_naive_time_is_rejected(self):
        item = observation("a", "north-a", "a")
        item["published_at"] = "2026-11-01T10:00:00"
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.ingest(item)
        self.assertEqual(caught.exception.code, "invalid_time")

    def test_invalid_stance_is_rejected_before_event(self):
        self.add("a")
        before = self.service.store.head()
        invalid = decision("a", "alice", "positive")
        invalid["stance"] = "unknown"
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(invalid)
        self.assertEqual(caught.exception.code, "invalid_stance")
        self.assertEqual(before, self.service.store.head())

    def test_source_registration_conflict(self):
        old = source("north-a", "North", "Outlet A")
        self.assertTrue(self.service.sources.register(old)["duplicate"])
        changed = old | {"outlet": "Renamed"}
        with self.assertRaises(MeridianError) as caught:
            self.service.sources.register(changed)
        self.assertEqual(caught.exception.code, "source_conflict")

    def test_query_rejects_duplicate_region_filter(self):
        invalid = query() | {"regions": ["North", "North"]}
        with self.assertRaises(MeridianError) as caught:
            self.service.analytics.report(invalid)
        self.assertEqual(caught.exception.code, "invalid_query")

    def test_report_explanation_distinguishes_missing_coding(self):
        self.add("a")
        result = self.service.diagnostics.observation_explanation("local-fixture", "a", query())
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reasons"], ["uncoded"])
        self.assertEqual(result["coding_status"], "awaiting_review")

    def test_review_queue_prioritizes_disputes(self):
        self.add("pending")
        self.add("disputed")
        self.service.coding.submit(decision("disputed", "alice", "positive"))
        self.service.coding.submit(decision("disputed", "bob", "negative"))
        queue = self.service.diagnostics.queue(query())
        self.assertEqual(queue["total"], 2)
        self.assertEqual(queue["items"][0]["status"], "disputed")
        self.assertEqual(queue["by_status"], {"disputed": 1, "uncoded": 1})

    def test_event_cursor_has_stable_order(self):
        self.add("a")
        self.add("b")
        first = self.service.audit.events(0, 2)
        second = self.service.audit.events(first[-1]["event_no"], 10)
        ids = [row["event_no"] for row in first + second]
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(ids[-1], self.service.store.head())

    def test_invalid_event_cursor_fails(self):
        with self.assertRaises(MeridianError) as caught:
            self.service.audit.events(-1, 2)
        self.assertEqual(caught.exception.code, "invalid_cursor")

    def test_region_window_comparison_requires_matching_filter(self):
        left = query()
        right = query() | {"regions": ["South"]}
        with self.assertRaises(MeridianError) as caught:
            self.service.comparisons.windows(left, right)
        self.assertEqual(caught.exception.code, "incompatible_query")

    def test_successful_bundle_survives_reopen(self):
        self.accepted("a")
        preview = self.service.releases.preview(query())
        self.service.releases.publish("release", query(), preview["token"])
        old = self.service.exports.frozen("release")
        self.service.close()
        self.service = MeridianService(self.path)
        new = self.service.exports.frozen("release")
        self.assertEqual(old["bundle_hash"], new["bundle_hash"])
        self.assertTrue(self.service.exports.verify(new)["valid"])


if __name__ == "__main__":
    unittest.main()
