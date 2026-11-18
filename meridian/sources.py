"""来源登记限制地区和允许的编码词表。"""
from .contracts import digest, source, utc_now
from .errors import MeridianError, require
from .storage import decode, encode


class SourceRegistry:
    def __init__(self, store):
        self.store = store

    def register(self, payload):
        with self.store.transaction():
            return self._register_locked(payload)

    def _register_locked(self, payload):
        data = source(payload)
        existing = self.store.one("SELECT * FROM sources WHERE source_id=?", (data["source_id"],))
        if existing:
            current = self.describe(data["source_id"])
            if all(current[key] == data[key] for key in data):
                return {**current, "duplicate": True}
            raise MeridianError("source_conflict", "source identifier already has different metadata")
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO sources(source_id,region,outlet,topics,audiences,active,created_at) VALUES(?,?,?,?,?,1,?)",
                (data["source_id"], data["region"], data["outlet"],
                 encode(data["topics"]), encode(data["audiences"]), now),
            )
            self.store.event("source_registered", data["source_id"], digest(data), now)
        return {**data, "active": True, "version": 1, "duplicate": False}

    def describe(self, source_id):
        row = self.store.one("SELECT * FROM sources WHERE source_id=?", (source_id,))
        require(row is not None, "source_missing", "source is not registered", source_id=source_id)
        return {"source_id": row["source_id"], "region": row["region"],
                "outlet": row["outlet"], "topics": decode(row["topics"]),
                "audiences": decode(row["audiences"]), "active": bool(row["active"]),
                "version": row["version"]}

    def list(self):
        return [self.describe(row["source_id"]) for row in self.store.all("SELECT source_id FROM sources ORDER BY source_id")]

    def deactivate(self, source_id):
        with self.store.transaction():
            return self._deactivate_locked(source_id)

    def _deactivate_locked(self, source_id):
        source_data = self.describe(source_id)
        if not source_data["active"]:
            return {**source_data, "duplicate": True}
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute("UPDATE sources SET active=0,version=version+1 WHERE source_id=?", (source_id,))
            self.store.event("source_deactivated", source_id, digest(source_data), now)
        return {**self.describe(source_id), "duplicate": False}

    def validate_for_ingest(self, source_id):
        data = self.describe(source_id)
        require(data["active"], "source_inactive", "source no longer accepts observations")
        return data

    def validate_coding(self, source_id, topic, audience):
        data = self.describe(source_id)
        require(topic in data["topics"], "topic_outside_source", "topic is not in source vocabulary")
        require(audience in data["audiences"], "audience_outside_source", "audience is not in source vocabulary")
        return data

