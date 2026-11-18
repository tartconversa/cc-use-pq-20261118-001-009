import unittest
from tests.common import ServiceCase
from meridian.demo import observation
from meridian.errors import MeridianError


class ObservationTests(ServiceCase):
    def test_repeat_is_idempotent_and_no_new_event(self):
        first = self.add("a")
        before = self.service.store.head()
        second = self.service.observations.ingest(observation("a", "north-a", "a"))
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(before, self.service.store.head())

    def test_conflicting_payload_requires_revision(self):
        self.add("a")
        changed = observation("a", "north-a", "new-story")
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.ingest(changed)
        self.assertEqual(caught.exception.code, "correction_required")
        self.assertEqual(self.service.observations.describe("local-fixture", "a")["revision"], 1)

    def test_correction_invalidates_old_coding(self):
        self.accepted("a")
        changed = observation("a", "north-a", "new-story")
        new = self.service.observations.ingest(changed, expected_revision=1)
        self.assertEqual(new["revision"], 2)
        self.assertEqual(self.report()["story_count"], 0)
        self.assertEqual(self.report()["excluded"]["uncoded"], 1)
        history = self.service.observations.lineage("local-fixture", "a")
        self.assertEqual([item["is_head"] for item in history], [False, True])

    def test_stale_revision_rejected(self):
        self.add("a")
        changed = observation("a", "north-a", "new-story")
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.ingest(changed, expected_revision=0)
        self.assertEqual(caught.exception.code, "revision_conflict")

    def test_correction_cannot_move_source(self):
        self.add("a")
        changed = observation("a", "north-b", "a")
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.ingest(changed, expected_revision=1)
        self.assertEqual(caught.exception.code, "source_identity_conflict")

    def test_missing_source_rejected_without_event(self):
        before = self.service.store.head()
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.ingest(observation("a", "unknown", "a"))
        self.assertEqual(caught.exception.code, "source_missing")
        self.assertEqual(before, self.service.store.head())

    def test_retraction_excludes_head_and_preserves_lineage(self):
        self.accepted("a")
        self.service.observations.retract("local-fixture", "a", "wrong link", expected_revision=1)
        report = self.report()
        self.assertEqual(report["story_count"], 0)
        self.assertEqual(report["excluded"], {"retracted": 1})
        self.assertEqual(self.service.observations.lineage("local-fixture", "a")[0]["reason"], "wrong link")

    def test_retract_again_fails(self):
        self.add("a")
        self.service.observations.retract("local-fixture", "a", "wrong", expected_revision=1)
        with self.assertRaises(MeridianError) as caught:
            self.service.observations.retract("local-fixture", "a", "wrong", expected_revision=1)
        self.assertEqual(caught.exception.code, "already_retracted")

    def test_outside_window_is_not_counted(self):
        self.service.observations.ingest(observation("old", "north-a", "old",
                                                     "2026-10-30T10:00:00+00:00"))
        self.assertEqual(self.report()["observations_in_window"], 0)

    def test_source_deactivation_blocks_new_data(self):
        self.service.sources.deactivate("north-a")
        with self.assertRaises(MeridianError) as caught:
            self.add("a")
        self.assertEqual(caught.exception.code, "source_inactive")

    def test_audit_detects_revision_integrity(self):
        self.add("a")
        self.assertTrue(self.service.audit.run()["ok"])
        self.service.store.db.execute("UPDATE observation_heads SET revision=99 WHERE origin='local-fixture'")
        audit = self.service.audit.run()
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["issues"][0]["kind"], "head_mismatch")


if __name__ == "__main__":
    unittest.main()
