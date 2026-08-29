"""
Web Intelligence subsystem (M7).

Provides search, fetch, extract, rank, and context-building capabilities
for answering questions that require current external information.

Public API:
    WebIntelligenceService  — orchestrates the full pipeline
    WebContext              — bounded, source-attributed evidence for the LLM
    WebSearchError          — raised when search completely fails
    FetchError              — raised when a page fetch fails
"""

from src.web.models import WebContext, WebDocument, WebSource
from src.web.service import WebIntelligenceService

__all__ = [
    "WebContext",
    "WebDocument",
    "WebSource",
    "WebIntelligenceService",
]
