import unittest
from tests.common import ServiceCase
from meridian.demo import decision
from meridian.errors import MeridianError


class CodingTests(ServiceCase):
    def test_one_review_is_pending(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        status = self.service.coding.status("local-fixture", "a", 1)
        self.assertEqual(status["status"], "awaiting_review")
        self.assertEqual(self.report()["excluded"], {"uncoded": 1})

    def test_matching_reviewers_accept(self):
        self.accepted("a")
        status = self.service.coding.status("local-fixture", "a", 1)
        self.assertEqual(status["status"], "consensus")
        self.assertEqual(status["coding"]["stance"], "positive")
        self.assertEqual(self.report()["story_count"], 1)

    def test_disagreement_blocks_report(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        self.service.coding.submit(decision("a", "bob", "negative"))
        self.assertEqual(self.report()["excluded"], {"disputed": 1})
        self.assertIn("unresolved_disputes", self.service.releases.preview(self.report()["query"])["quality"]["reasons"])

    def test_third_party_adjudication_resolves(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        self.service.coding.submit(decision("a", "bob", "negative"))
        ruling = {"origin": "local-fixture", "external_id": "a", "revision": 1,
                  "adjudicator": "carol", "topic": "technology", "stance": "mixed",
                  "audience": "general", "reason": "Different claims in same item"}
        result = self.service.coding.adjudicate(ruling)
        self.assertEqual(result["status"], "adjudicated")
        self.assertEqual(self.report()["regions"][0]["stance_counts"], {"mixed": 1})

    def test_reviewer_cannot_adjudicate(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        self.service.coding.submit(decision("a", "bob", "negative"))
        ruling = {"origin": "local-fixture", "external_id": "a", "revision": 1,
                  "adjudicator": "alice", "topic": "technology", "stance": "positive",
                  "audience": "general", "reason": "I prefer my own answer"}
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.adjudicate(ruling)
        self.assertEqual(caught.exception.code, "self_adjudication")

    def test_topic_not_permitted(self):
        self.add("a")
        wrong = decision("a", "alice", "positive")
        wrong["topic"] = "sports"
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(wrong)
        self.assertEqual(caught.exception.code, "topic_outside_source")

    def test_reviewer_can_submit_only_once(self):
        self.add("a")
        self.service.coding.submit(decision("a", "alice", "positive"))
        alternate = decision("a", "alice", "negative")
        alternate["decision_id"] = "alternate"
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(alternate)
        self.assertEqual(caught.exception.code, "reviewer_conflict")

    def test_identical_decision_is_idempotent(self):
        self.add("a")
        item = decision("a", "alice", "positive")
        first = self.service.coding.submit(item)
        head = self.service.store.head()
        second = self.service.coding.submit(item)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(head, self.service.store.head())

    def test_stale_revision_cannot_receive_new_coding(self):
        self.add("a")
        changed = self.service.observations.describe("local-fixture", "a")["data"] | {"story_key": "updated"}
        self.service.observations.ingest(changed, expected_revision=1)
        with self.assertRaises(MeridianError) as caught:
            self.service.coding.submit(decision("a", "alice", "positive"))
        self.assertEqual(caught.exception.code, "stale_observation")


if __name__ == "__main__":
    unittest.main()
