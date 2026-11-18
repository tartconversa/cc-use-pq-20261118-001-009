"""绑定清单的可恢复逐行导入。"""
from .contracts import digest, text, utc_now
from .errors import MeridianError, require
from .storage import decode, encode


class BatchImporter:
    def __init__(self, store, observations):
        self.store = store
        self.observations = observations

    def begin(self, batch_id, rows, *, expected_count=None, manifest_hash=None):
        batch_id = text(batch_id, "batch_id")
        require(isinstance(rows, list), "invalid_batch", "rows must be list")
        require(len(rows) <= 10000, "batch_too_large", "batch exceeds local limit")
        expected_count = len(rows) if expected_count is None else expected_count
        require(type(expected_count) is int and expected_count == len(rows), "count_mismatch",
                "manifest count differs from supplied rows")
        actual_hash = digest(rows)
        if manifest_hash is not None:
            require(manifest_hash == actual_hash, "manifest_mismatch", "manifest hash differs from rows")
        existing = self.store.one("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        if existing:
            require(existing["manifest_hash"] == actual_hash and existing["expected_count"] == expected_count,
                    "batch_conflict", "batch identifier has a different manifest")
            return self.status(batch_id) | {"duplicate": True}
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO batches(batch_id,manifest_hash,expected_count,state,cursor,accepted,duplicate_count,rejected,created_at) "
                "VALUES(?,?,?,'open',0,0,0,0,?)", (batch_id, actual_hash, expected_count, now))
            self.store.event("batch_opened", batch_id, actual_hash, now)
        return self.status(batch_id) | {"duplicate": False}

    def step(self, batch_id, rows, *, limit=None):
        current = self.begin(batch_id, rows)
        start = current["cursor"]
        if limit is None:
            limit = len(rows) - start
        require(type(limit) is int and limit >= 0, "invalid_limit", "limit must be nonnegative")
        stop = min(len(rows), start + limit)
        for index in range(start, stop):
            row = rows[index]
            row_hash = digest(row)
            try:
                result = self.observations.ingest(row)
                state = "duplicate" if result["duplicate"] else "accepted"
                error = None
            except MeridianError as exc:
                state = "rejected"
                error = exc.as_dict()
                result = None
            with self.store.transaction():
                self.store.db.execute(
                    "INSERT INTO batch_rows(batch_id,row_no,row_hash,status,error,result) VALUES(?,?,?,?,?,?)",
                    (batch_id, index, row_hash, state, encode(error) if error else None,
                     encode(result) if result else None))
                self.store.db.execute(
                    "UPDATE batches SET cursor=?,accepted=accepted+?,duplicate_count=duplicate_count+?,"
                    "rejected=rejected+?,state=? WHERE batch_id=?",
                    (index + 1, int(state == "accepted"), int(state == "duplicate"),
                     int(state == "rejected"), "closed" if index + 1 == len(rows) else "open", batch_id))
        if not rows and current["state"] == "open":
            with self.store.transaction():
                self.store.db.execute("UPDATE batches SET state='closed' WHERE batch_id=?", (batch_id,))
        return self.status(batch_id)

    def status(self, batch_id):
        row = self.store.one("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        require(row is not None, "batch_missing", "batch not found")
        outcomes = self.store.all("SELECT * FROM batch_rows WHERE batch_id=? ORDER BY row_no", (batch_id,))
        return {"batch_id": batch_id, "manifest_hash": row["manifest_hash"],
                "expected_count": row["expected_count"], "state": row["state"],
                "cursor": row["cursor"], "accepted": row["accepted"],
                "duplicate_count": row["duplicate_count"], "rejected": row["rejected"],
                "outcomes": [{"row_no": item["row_no"], "status": item["status"],
                              "error": decode(item["error"]) if item["error"] else None}
                             for item in outcomes]}

