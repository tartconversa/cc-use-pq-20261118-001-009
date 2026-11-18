import tempfile
import unittest
from pathlib import Path
from meridian.demo import decision, observation, query, source
from meridian.service import MeridianService


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "db.sqlite3")
        self.service = MeridianService(self.path)
        self.service.sources.register(source("north-a", "North", "Outlet A"))
        self.service.sources.register(source("north-b", "North", "Outlet B"))
        self.service.sources.register(source("south-a", "South", "Outlet C"))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def add(self, external_id, source_id="north-a", story_key=None):
        return self.service.observations.ingest(observation(external_id, source_id, story_key or external_id))

    def code(self, external_id, stance="positive", *, source_id="north-a"):
        self.service.coding.submit(decision(external_id, "alice", stance))
        self.service.coding.submit(decision(external_id, "bob", stance))

    def accepted(self, external_id, source_id="north-a", story_key=None):
        self.add(external_id, source_id, story_key)
        self.code(external_id, source_id=source_id)

    def report(self):
        return self.service.analytics.report(query())
