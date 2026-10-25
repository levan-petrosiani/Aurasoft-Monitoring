"""Loads the documents listed in standards/sources.yaml into the store as clauses, with local bge-m3 embeddings for
search by meaning. Embedding failures do not stop the ingest: full-text search still works."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..standards import StandardSource, StandardSources
from ..store.entities import ClauseRow, StandardDocumentRow
from .chat import ChatFactory, ProviderError

# bge-m3 reads up to 8192 tokens; Georgian text needs about one token per character, so stay well below.
MAX_EMBEDDING_CHARS = 4000
_BATCH = 16


@dataclass
class IngestResult:
    standard: str
    version: str
    clauses: int
    embedded: int
    problems: list[str] = field(default_factory=list)
    duration_ms: int = 0


def embedding_text(c: ClauseRow) -> str:
    """What a clause is searched by: number, title and the start of its text."""
    return f"{c.number} {c.title}\n{c.text}"[:MAX_EMBEDDING_CHARS]


class StandardsIngestor:
    def __init__(self, store, sources: StandardSources, chats: ChatFactory):
        self._store, self._sources, self._chats = store, sources, chats

    def ingest(self, source: StandardSource, embed: bool = True, progress=None) -> IngestResult:
        started = time.monotonic()
        clauses, problems = self._sources.read(source)
        rows = [ClauseRow(standard=c.standard, version=c.version, number=c.number, title=c.title, text=c.text,
                          level=c.level, ord=c.order, language=c.language, key=c.key, parent=c.parent, page=c.page)
                for c in clauses]
        self._store.replace_standard(StandardDocumentRow(
            id=source.id, version=source.version, title=source.title, language=source.language, url=source.url,
            sha256=source.sha256, publisher=source.publisher, license=source.license,
            ingested_utc=datetime.now(timezone.utc), clause_count=len(rows)), rows)
        if progress:
            progress(f"{source.id} {source.version}: {len(rows)} clauses stored")

        embedded = 0
        if embed:
            try:
                for i in range(0, len(rows), _BATCH):
                    batch = rows[i:i + _BATCH]
                    vectors = self._chats.embed([embedding_text(c) for c in batch])
                    self._store.set_clause_embeddings([(c.id, v) for c, v in zip(batch, vectors)])
                    embedded += len(batch)
                    if progress:
                        progress(f"  embedded {embedded}/{len(rows)}")
            except (ProviderError, TimeoutError) as ex:
                problems.append(f"Embeddings skipped ({ex}); full-text search still works. Is Ollama running with "
                                f"{self._chats.options.ollama.embedding_model}?")
        return IngestResult(source.id, source.version, len(rows), embedded, problems, int((time.monotonic() - started) * 1000))
