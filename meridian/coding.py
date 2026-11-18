"""独立人工编码及争议裁定，不自动推断倾向。"""
from .contracts import adjudication, decision, digest, utc_now
from .errors import MeridianError, require
from .storage import decode, encode


class CodingDesk:
    def __init__(self, store, observations, sources):
        self.store = store
        self.observations = observations
        self.sources = sources

    def _eligible(self, origin, external_id, revision):
        item = self.observations.describe(origin, external_id, revision)
        require(item["is_head"] and item["status"] == "active", "stale_observation",
                "coding requires active head revision")
        return item

    def submit(self, payload):
        with self.store.transaction():
            return self._submit_locked(payload)

    def _submit_locked(self, payload):
        data = decision(payload)
        existing = self.store.one("SELECT * FROM decisions WHERE decision_id=?", (data["decision_id"],))
        if existing:
            saved = self.describe(data["decision_id"])
            if all(saved[key] == data[key] for key in data):
                return saved | {"duplicate": True}
            raise MeridianError("decision_conflict", "decision id was reused with different content")
        item = self._eligible(data["origin"], data["external_id"], data["revision"])
        self.sources.validate_coding(item["data"]["source_id"], data["topic"], data["audience"])
        key = (data["origin"], data["external_id"], data["revision"], data["reviewer"])
        previous = self.store.one("SELECT decision_id FROM decisions WHERE origin=? AND external_id=? "
                                  "AND revision=? AND reviewer=?", key)
        require(previous is None, "reviewer_conflict", "reviewer already coded this revision")
        now = utc_now()
        with self.store.transaction():
            event_no = self.store.event("coding_submitted", data["decision_id"], digest(data), now)
            self.store.db.execute(
                "INSERT INTO decisions(decision_id,origin,external_id,revision,reviewer,topic,stance,"
                "audience,reason,created_at,event_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (data["decision_id"], *key, data["topic"], data["stance"],
                 data["audience"], data["reason"], now, event_no),
            )
        return self.describe(data["decision_id"]) | {"duplicate": False}

    def describe(self, decision_id):
        row = self.store.one("SELECT * FROM decisions WHERE decision_id=?", (decision_id,))
        require(row is not None, "decision_missing", "decision not found")
        return {key: row[key] for key in ("decision_id", "origin", "external_id", "revision",
                                           "reviewer", "topic", "stance", "audience", "reason",
                                           "event_no", "created_at")}

    def decisions_for(self, origin, external_id, revision):
        rows = self.store.all("SELECT decision_id FROM decisions WHERE origin=? AND external_id=? "
                              "AND revision=? ORDER BY reviewer", (origin, external_id, revision))
        return [self.describe(row["decision_id"]) for row in rows]

    def status(self, origin, external_id, revision):
        decisions = self.decisions_for(origin, external_id, revision)
        if len(decisions) < 2:
            return {"status": "awaiting_review", "decisions": decisions}
        values = {(row["topic"], row["stance"], row["audience"]) for row in decisions}
        if len(values) == 1:
            return {"status": "consensus", "decisions": decisions,
                    "coding": dict(zip(("topic", "stance", "audience"), next(iter(values))))}
        key = f"{origin}/{external_id}/{revision}"
        adjudicated = self.store.one("SELECT * FROM adjudications WHERE case_key=?", (key,))
        if adjudicated:
            heads = [row["decision_id"] for row in decisions]
            if decode(adjudicated["decision_heads"]) == heads:
                return {"status": "adjudicated", "decisions": decisions,
                        "coding": {key: adjudicated[key] for key in ("topic", "stance", "audience")},
                        "adjudicator": adjudicated["adjudicator"]}
        return {"status": "disputed", "decisions": decisions}

    def adjudicate(self, payload):
        with self.store.transaction():
            return self._adjudicate_locked(payload)

    def _adjudicate_locked(self, payload):
        data = adjudication(payload)
        item = self._eligible(data["origin"], data["external_id"], data["revision"])
        self.sources.validate_coding(item["data"]["source_id"], data["topic"], data["audience"])
        status = self.status(data["origin"], data["external_id"], data["revision"])
        require(status["status"] in {"disputed", "adjudicated"}, "adjudication_not_needed",
                "only disagreement can be adjudicated")
        require(data["adjudicator"] not in {row["reviewer"] for row in status["decisions"]},
                "self_adjudication", "reviewer cannot adjudicate own case")
        key = f"{data['origin']}/{data['external_id']}/{data['revision']}"
        heads = [row["decision_id"] for row in status["decisions"]]
        existing = self.store.one("SELECT * FROM adjudications WHERE case_key=?", (key,))
        if existing:
            same = (all(existing[field] == data[field] for field in ("adjudicator", "topic",
                                                                  "stance", "audience", "reason"))
                    and decode(existing["decision_heads"]) == heads)
            require(same, "adjudication_conflict", "case already has another ruling")
            return self.status(data["origin"], data["external_id"], data["revision"]) | {"duplicate": True}
        now = utc_now()
        with self.store.transaction():
            event_no = self.store.event("case_adjudicated", key, digest(data), now)
            self.store.db.execute(
                "INSERT INTO adjudications(case_key,origin,external_id,revision,adjudicator,topic,"
                "stance,audience,reason,decision_heads,created_at,event_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, data["origin"], data["external_id"], data["revision"], data["adjudicator"],
                 data["topic"], data["stance"], data["audience"], data["reason"],
                 encode(heads), now, event_no),
            )
        return self.status(data["origin"], data["external_id"], data["revision"]) | {"duplicate": False}

