"""Explicit no-op telemetry adapter for the embedded Chroma client."""

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent
from overrides import override


class NoOpProductTelemetry(ProductTelemetryClient):
    """Keep local document and usage metadata entirely on the host."""

    @override
    def capture(self, event: ProductTelemetryEvent) -> None:
        del event
