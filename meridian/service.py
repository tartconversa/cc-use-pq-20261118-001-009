"""连接来源、证据、编码、分析和发布的应用服务。"""
from .analytics import EvidenceAnalytics
from .audit import IntegrityAudit
from .batches import BatchImporter
from .coding import CodingDesk
from .comparisons import ComparisonEngine
from .diagnostics import ReviewDiagnostics
from .exports import EvidenceExporter
from .frames import CollectionFrames
from .observations import ObservationLedger
from .provenance import ProvenanceExplorer
from .releases import ReleaseShelf
from .sources import SourceRegistry
from .storage import Store


class MeridianService:
    def __init__(self, path):
        self.store = Store(path)
        self.sources = SourceRegistry(self.store)
        self.observations = ObservationLedger(self.store, self.sources)
        self.coding = CodingDesk(self.store, self.observations, self.sources)
        self.frames = CollectionFrames(self.store, self.sources, self.observations)
        self.analytics = EvidenceAnalytics(self.store, self.observations, self.sources, self.coding)
        self.audit = IntegrityAudit(self.store)
        self.comparisons = ComparisonEngine(self.analytics)
        self.diagnostics = ReviewDiagnostics(self.store, self.observations, self.sources,
                                              self.coding, self.analytics, self.frames)
        self.releases = ReleaseShelf(self.store, self.analytics, self.frames, self.audit)
        self.provenance = ProvenanceExplorer(self.store, self.observations, self.coding,
                                              self.analytics, self.releases)
        self.exports = EvidenceExporter(self.store, self.observations, self.sources,
                                         self.coding, self.analytics, self.releases)
        self.batches = BatchImporter(self.store, self.observations)

    def close(self):
        self.store.close()

    def status(self):
        return {"event_head": self.store.head(),
                "sources": len(self.sources.list()),
                "observations": len(self.observations.current()),
                "releases": len(self.store.all("SELECT release_id FROM releases"))}

