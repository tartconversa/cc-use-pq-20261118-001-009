"""来源与发布窗口的采集覆盖约定。"""
from .contracts import digest, text, timestamp, utc_now
from .errors import MeridianError, require


class CollectionFrames:
    def __init__(self, store, sources, observations):
        self.store = store
        self.sources = sources
        self.observations = observations

    def open(self, frame_id, source_id, start, end, expected_count):
        with self.store.transaction():
            return self._open_locked(frame_id, source_id, start, end, expected_count)

    def _open_locked(self, frame_id, source_id, start, end, expected_count):
        frame_id = text(frame_id, "frame_id")
        source_id = text(source_id, "source_id")
        start = timestamp(start, "start")
        end = timestamp(end, "end")
        require(start < end, "invalid_window", "frame start must precede end")
        require(type(expected_count) is int and expected_count >= 0, "invalid_expected_count",
                "expected_count must be nonnegative")
        self.sources.validate_for_ingest(source_id)
        existing = self.store.one("SELECT * FROM collection_frames WHERE frame_id=?", (frame_id,))
        if existing:
            same = all((existing["source_id"] == source_id, existing["start_at"] == start,
                        existing["end_at"] == end, existing["expected_count"] == expected_count))
            require(same, "frame_conflict", "frame identifier has different coverage contract")
            return self.describe(frame_id) | {"duplicate": True}
        overlapping = self.store.one(
            "SELECT frame_id FROM collection_frames WHERE source_id=? AND start_at<? AND end_at>?",
            (source_id, end, start))
        require(overlapping is None, "frame_overlap", "source coverage windows overlap")
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO collection_frames(frame_id,source_id,start_at,end_at,expected_count,state,created_at) "
                "VALUES(?,?,?,?,?,'open',?)", (frame_id, source_id, start, end, expected_count, now))
            self.store.event("frame_opened", frame_id, digest([source_id, start, end, expected_count]), now)
        return self.describe(frame_id) | {"duplicate": False}

    def _candidates(self, frame):
        rows = self.store.all(
            "SELECT o.* FROM observations o JOIN observation_heads h ON o.origin=h.origin "
            "AND o.external_id=h.external_id AND o.revision=h.revision WHERE o.source_id=? "
            "AND o.published_at>=? AND o.published_at<? AND o.status='active' "
            "ORDER BY o.origin,o.external_id",
            (frame["source_id"], frame["start"], frame["end"]))
        return [{"origin": row["origin"], "external_id": row["external_id"],
                 "revision": row["revision"], "hash": row["payload_hash"]} for row in rows]

    def describe(self, frame_id):
        row = self.store.one("SELECT * FROM collection_frames WHERE frame_id=?", (frame_id,))
        require(row is not None, "frame_missing", "frame not found")
        data = {"frame_id": frame_id, "source_id": row["source_id"], "start": row["start_at"],
                "end": row["end_at"], "expected_count": row["expected_count"],
                "state": row["state"], "actual_count": row["actual_count"],
                "frozen_hash": row["frozen_hash"]}
        candidates = self._candidates(data)
        data["current_count"] = len(candidates)
        data["current_hash"] = digest(candidates)
        data["drifted"] = row["state"] == "closed" and data["current_hash"] != data["frozen_hash"]
        return data

    def close(self, frame_id):
        with self.store.transaction():
            return self._close_locked(frame_id)

    def _close_locked(self, frame_id):
        frame = self.describe(frame_id)
        if frame["state"] == "closed":
            require(not frame["drifted"], "frame_drift", "closed frame changed after sealing")
            return frame | {"duplicate": True}
        require(frame["current_count"] == frame["expected_count"], "frame_incomplete",
                "observed count differs from declared coverage", expected=frame["expected_count"],
                actual=frame["current_count"])
        now = utc_now()
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE collection_frames SET state='closed',actual_count=?,frozen_hash=?,closed_at=? WHERE frame_id=?",
                (frame["current_count"], frame["current_hash"], now, frame_id))
            for item in self._candidates(frame):
                self.store.db.execute(
                    "INSERT INTO frame_members(frame_id,origin,external_id,revision) VALUES(?,?,?,?)",
                    (frame_id, item["origin"], item["external_id"], item["revision"]))
            self.store.event("frame_closed", frame_id, frame["current_hash"], now)
        return self.describe(frame_id) | {"duplicate": False}

    def coverage(self, spec):
        sources = [row for row in self.sources.list() if not spec["regions"] or row["region"] in spec["regions"]]
        result = []
        for source in sources:
            frames = self.store.all(
                "SELECT frame_id FROM collection_frames WHERE source_id=? AND start_at>=? AND end_at<=? "
                "ORDER BY start_at", (source["source_id"], spec["start"], spec["end"]))
            for row in frames:
                result.append(self.describe(row["frame_id"]))
        return result

    def quality(self, spec):
        frames = self.coverage(spec)
        problems = []
        for frame in frames:
            if frame["state"] != "closed":
                problems.append({"frame_id": frame["frame_id"], "reason": "open"})
            elif frame["drifted"]:
                problems.append({"frame_id": frame["frame_id"], "reason": "drifted"})
        return {"frames": frames, "problems": problems, "passed": not problems}

