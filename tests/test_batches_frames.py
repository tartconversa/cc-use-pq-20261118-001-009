import unittest
from tests.common import ServiceCase
from meridian.demo import observation, query
from meridian.errors import MeridianError


class BatchAndFrameTests(ServiceCase):
    def test_batch_resumes_after_partial_step(self):
        rows = [observation("a", "north-a", "a"), observation("b", "north-a", "b")]
        first = self.service.batches.step("batch", rows, limit=1)
        self.assertEqual(first["cursor"], 1)
        self.assertEqual(first["state"], "open")
        resumed = self.service.batches.step("batch", rows)
        self.assertEqual(resumed["cursor"], 2)
        self.assertEqual(resumed["accepted"], 2)
        self.assertEqual(resumed["state"], "closed")

    def test_batch_rejects_bad_row_without_partial_observation(self):
        bad = observation("bad", "missing", "bad")
        rows = [observation("good", "north-a", "good"), bad]
        result = self.service.batches.step("mixed", rows)
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["outcomes"][1]["error"]["error"], "source_missing")
        self.assertEqual(len(self.service.observations.current()), 1)

    def test_batch_is_idempotent_across_repeated_call(self):
        rows = [observation("a", "north-a", "a")]
        before = self.service.batches.step("batch", rows)
        event_head = self.service.store.head()
        after = self.service.batches.step("batch", rows)
        self.assertEqual(before["outcomes"], after["outcomes"])
        self.assertEqual(event_head, self.service.store.head())

    def test_reusing_batch_identifier_with_different_manifest_fails(self):
        first = [observation("a", "north-a", "a")]
        second = [observation("b", "north-a", "b")]
        self.service.batches.begin("batch", first)
        with self.assertRaises(MeridianError) as caught:
            self.service.batches.begin("batch", second)
        self.assertEqual(caught.exception.code, "batch_conflict")

    def test_declared_count_must_match_rows(self):
        with self.assertRaises(MeridianError) as caught:
            self.service.batches.begin("batch", [], expected_count=1)
        self.assertEqual(caught.exception.code, "count_mismatch")

    def test_frame_closes_only_when_count_matches(self):
        self.service.frames.open("frame", "north-a", query()["start"], query()["end"], 1)
        with self.assertRaises(MeridianError) as caught:
            self.service.frames.close("frame")
        self.assertEqual(caught.exception.code, "frame_incomplete")
        self.add("a")
        closed = self.service.frames.close("frame")
        self.assertEqual(closed["state"], "closed")
        self.assertEqual(closed["actual_count"], 1)

    def test_overlapping_source_frames_fail(self):
        self.service.frames.open("frame", "north-a", query()["start"], query()["end"], 0)
        with self.assertRaises(MeridianError) as caught:
            self.service.frames.open("overlap", "north-a", "2026-11-15T00:00:00Z",
                                     "2026-12-15T00:00:00Z", 0)
        self.assertEqual(caught.exception.code, "frame_overlap")

    def test_closed_frame_detects_later_correction(self):
        self.add("a")
        self.service.frames.open("frame", "north-a", query()["start"], query()["end"], 1)
        self.service.frames.close("frame")
        changed = observation("a", "north-a", "updated")
        self.service.observations.ingest(changed, expected_revision=1)
        status = self.service.frames.describe("frame")
        self.assertTrue(status["drifted"])
        self.assertFalse(self.service.frames.quality(query())["passed"])

    def test_open_frame_blocks_preview_quality(self):
        self.accepted("a")
        self.service.frames.open("frame", "north-a", query()["start"], query()["end"], 1)
        preview = self.service.releases.preview(query())
        self.assertFalse(preview["quality"]["passed"])
        self.assertIn("collection_coverage", preview["quality"]["reasons"])

    def test_audit_reconciles_frame_members(self):
        self.add("a")
        self.service.frames.open("frame", "north-a", query()["start"], query()["end"], 1)
        self.service.frames.close("frame")
        self.assertTrue(self.service.audit.run()["ok"])
        self.service.store.db.execute("DELETE FROM frame_members WHERE frame_id='frame'")
        self.assertIn("frame_members", [item["kind"] for item in self.service.audit.run()["issues"]])


if __name__ == "__main__":
    unittest.main()
