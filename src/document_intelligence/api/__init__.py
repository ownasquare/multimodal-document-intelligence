"""Versioned HTTP boundary for the document-intelligence workspace."""

from document_intelligence.api.app import create_app
from document_intelligence.api.contracts import ApplicationServices

__all__ = ["ApplicationServices", "create_app"]
