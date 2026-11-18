"""不可变观察修订与明确的活动版本。"""
from .contracts import digest, observation, utc_now
from .errors import MeridianError, require
from .storage import decode, encode


class ObservationLedger:
    def __init__(self, store, sources):
        self.store = store
        self.sources = sources

    def ingest(self, payload, *, expected_revision=None):
        with self.store.transaction():
            return self._ingest_locked(payload, expected_revision=expected_revision)

    def _ingest_locked(self, payload, *, expected_revision=None):
        data = observation(payload)
        self.sources.validate_for_ingest(data["source_id"])
        key = (data["origin"], data["external_id"])
        existing = self.store.one(
            "SELECT o.* FROM observations o JOIN observation_heads h "
            "ON h.origin=o.origin AND h.external_id=o.external_id AND h.revision=o.revision "
            "WHERE o.origin=? AND o.external_id=?", key)
        fingerprint = digest(data)
        if existing and existing["payload_hash"] == fingerprint:
            return self.describe(*key, existing["revision"]) | {"duplicate": True}
        if existing:
            require(expected_revision is not None, "correction_required",
                    "different payload requires an expected revision")
            require(type(expected_revision) is int and expected_revision == existing["revision"],
                    "revision_conflict", "head changed before correction", current=existing["revision"])
            require(existing["source_id"] == data["source_id"], "source_identity_conflict",
                    "correction cannot change source identity")
        else:
            require(expected_revision is None, "revision_conflict", "new observation cannot expect old revision")
        revision = (existing["revision"] + 1) if existing else 1
        now = utc_now()
        with self.store.transaction():
            head = self.store.one("SELECT revision FROM observation_heads WHERE origin=? AND external_id=?", key)
            require((head is None and existing is None) or (head is not None and existing is not None
                    and head["revision"] == existing["revision"]),
                    "head_mismatch", "observation head is inconsistent")
            self.store.db.execute(
                "INSERT INTO observations(origin,external_id,revision,payload_hash,payload,story_key,"
                "source_id,published_at,received_at,status,reason,supersedes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (*key, revision, fingerprint, encode(data), data["story_key"], data["source_id"],
                 data["published_at"], data["received_at"], "active", None,
                 existing["revision"] if existing else None),
            )
            if existing:
                self.store.db.execute(
                    "UPDATE observation_heads SET revision=? WHERE origin=? AND external_id=?",
                    (revision, *key))
            else:
                self.store.db.execute(
                    "INSERT INTO observation_heads(origin,external_id,revision) VALUES(?,?,?)",
                    (*key, revision))
            self.store.event("observation_corrected" if existing else "observation_ingested",
                             "/".join(key), fingerprint, now)
        return self.describe(*key, revision) | {"duplicate": False}

    def describe(self, origin, external_id, revision=None):
        if revision is None:
            head = self.store.one("SELECT revision FROM observation_heads WHERE origin=? AND external_id=?",
                                  (origin, external_id))
            require(head is not None, "observation_missing", "observation not found")
            revision = head["revision"]
        row = self.store.one(
            "SELECT * FROM observations WHERE origin=? AND external_id=? AND revision=?",
            (origin, external_id, revision))
        require(row is not None, "observation_missing", "observation revision not found")
        head = self.store.one("SELECT revision FROM observation_heads WHERE origin=? AND external_id=?",
                              (origin, external_id))
        return {"data": decode(row["payload"]), "revision": revision, "is_head": revision == head["revision"],
                "status": row["status"], "reason": row["reason"], "hash": row["payload_hash"],
                "supersedes": row["supersedes"]}

    def retract(self, origin, external_id, reason, *, expected_revision):
        with self.store.transaction():
            return self._retract_locked(origin, external_id, reason, expected_revision=expected_revision)

    def _retract_locked(self, origin, external_id, reason, *, expected_revision):
        current = self.describe(origin, external_id)
        require(current["revision"] == expected_revision, "revision_conflict", "head changed")
        require(current["status"] == "active", "already_retracted", "observation is retracted")
        from .contracts import text
        reason = text(reason, "reason", 800)
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute("UPDATE observations SET status='retracted',reason=? "
                                  "WHERE origin=? AND external_id=? AND revision=?",
                                  (reason, origin, external_id, expected_revision))
            self.store.event("observation_retracted", f"{origin}/{external_id}", digest(reason), now)
        return self.describe(origin, external_id)

    def current(self):
        rows = self.store.all(
            "SELECT o.* FROM observations o JOIN observation_heads h "
            "ON o.origin=h.origin AND o.external_id=h.external_id AND o.revision=h.revision "
            "ORDER BY o.origin,o.external_id")
        return [self.describe(row["origin"], row["external_id"], row["revision"]) for row in rows]

    def lineage(self, origin, external_id):
        rows = self.store.all("SELECT revision FROM observations WHERE origin=? AND external_id=? ORDER BY revision",
                              (origin, external_id))
        require(rows, "observation_missing", "observation not found")
        return [self.describe(origin, external_id, row["revision"]) for row in rows]

