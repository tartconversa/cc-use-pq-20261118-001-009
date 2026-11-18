import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from meridian.api import create_app
from meridian.demo import decision, observation, query, source
from meridian.errors import MeridianError
from tests.common import ServiceCase


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = str(Path(self.temp.name) / "api.sqlite3")
        self.app = create_app(path)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.state.service.close()
        self.temp.cleanup()

    def test_full_http_publication_and_export(self):
        registered = self.client.post("/sources", json=source("a", "North", "Outlet A"))
        self.assertEqual(registered.status_code, 200)
        ingested = self.client.post("/observations", json=observation("story", "a", "one"))
        self.assertEqual(ingested.status_code, 200)
        for reviewer in ("alice", "bob"):
            coded = self.client.post("/decisions", json=decision("story", reviewer, "positive"))
            self.assertEqual(coded.status_code, 200)
        preview = self.client.post("/reports/preview", json=query()).json()
        self.assertEqual(preview["report"]["story_count"], 1)
        published = self.client.post("/releases", json={"release_id": "november",
                                                        "query": query(), "token": preview["token"]})
        self.assertEqual(published.status_code, 200)
        exported = self.client.get("/releases/november/export").json()
        self.assertEqual(exported["report"]["story_count"], 1)
        self.assertTrue(self.app.state.service.exports.verify(exported)["valid"])
        self.assertTrue(self.client.get("/audit").json()["ok"])

    def test_http_conflict_and_missing_error_codes(self):
        self.client.post("/sources", json=source("a", "North", "Outlet A"))
        self.client.post("/observations", json=observation("story", "a", "one"))
        changed = observation("story", "a", "two")
        conflict = self.client.post("/observations", json=changed)
        self.assertEqual(conflict.status_code, 422)
        self.assertEqual(conflict.json()["error"], "correction_required")
        missing = self.client.get("/releases/unknown")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"], "release_missing")

    def test_http_batch_resume_and_status(self):
        self.client.post("/sources", json=source("a", "North", "Outlet A"))
        rows = [observation("x", "a", "x"), observation("y", "a", "y")]
        first = self.client.post("/batches", json={"batch_id": "b", "rows": rows, "limit": 1})
        self.assertEqual(first.json()["cursor"], 1)
        resumed = self.client.post("/batches", json={"batch_id": "b", "rows": rows})
        self.assertEqual(resumed.json()["accepted"], 2)
        status = self.client.get("/batches/b")
        self.assertEqual(status.json()["state"], "closed")

    def test_http_frame_open_close_and_readiness(self):
        self.client.post("/sources", json=source("a", "North", "Outlet A"))
        self.client.post("/observations", json=observation("x", "a", "x"))
        for reviewer in ("alice", "bob"):
            self.client.post("/decisions", json=decision("x", reviewer, "positive"))
        frame = {"frame_id": "f", "source_id": "a", "start": query()["start"],
                 "end": query()["end"], "expected_count": 1}
        self.assertEqual(self.client.post("/frames", json=frame).status_code, 200)
        self.assertFalse(self.client.post("/reports/readiness", json=query()).json()["ready"])
        closed = self.client.post("/frames/f/close")
        self.assertEqual(closed.status_code, 200)
        self.assertTrue(self.client.post("/reports/readiness", json=query()).json()["ready"])

    def test_http_validation_rejects_bad_shape(self):
        response = self.client.post("/reports", json={"start": "bad"})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/sources", json={"source_id": "only-id"})
        self.assertEqual(response.status_code, 422)


class ExportTests(ServiceCase):
    def test_current_bundle_reconciles_evidence(self):
        self.accepted("a")
        self.add("pending")
        bundle = self.service.exports.current(query())
        self.assertEqual(len(bundle["evidence"]), 2)
        self.assertEqual(bundle["report"]["eligible_observations"], 1)
        self.assertTrue(self.service.exports.verify(bundle)["valid"])

    def test_tampered_bundle_is_rejected(self):
        self.accepted("a")
        bundle = self.service.exports.current(query())
        bundle["report"]["story_count"] = 99
        with self.assertRaises(MeridianError) as caught:
            self.service.exports.verify(bundle)
        self.assertEqual(caught.exception.code, "bundle_hash_mismatch")

    def test_source_breakdown_matches_report(self):
        self.accepted("a")
        self.add("pending")
        result = self.service.exports.source_breakdown(query())
        self.assertEqual(result["total_observations"], 2)
        self.assertEqual(result["sources"][0]["categories"], {"eligible": 1, "uncoded": 1})

    def test_story_evidence_reconciles_cross_outlet(self):
        self.accepted("a", "north-a", "shared")
        self.accepted("b", "north-b", "shared")
        result = self.service.exports.story_evidence(query(), "North", "shared")
        self.assertEqual(len(result["evidence"]), 2)
        self.assertEqual(result["story"][0]["observation_count"], 2)

    def test_source_contribution_tracks_eligible_and_excluded(self):
        self.accepted("a")
        self.add("pending")
        result = self.service.provenance.source_contribution(query(), "north-a")
        self.assertEqual(result["observation_count"], 2)
        self.assertEqual(result["categories"], {"eligible": 1, "uncoded": 1})

    def test_coding_lineage_exposes_two_reviewers(self):
        self.accepted("a")
        result = self.service.provenance.coding_lineage("local-fixture", "a")
        self.assertEqual(result["status"], "consensus")
        self.assertEqual(len(result["decisions"]), 2)


if __name__ == "__main__":
    unittest.main()
