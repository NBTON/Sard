"""L0 Evidence Layer for Sard's Isnād Memory.

Preserves immutable raw evidence from RAG chunks, Parallel Search pages,
image-ID traces, and tool extraction logs. Every item has a durable source_id
and a raw_ref pointer back to the exact unsummarized text.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sard.schemas.isnad import Evidence, Region, SourceType


class L0EvidenceStore:
    """Store for L0 immutable evidence items."""

    def __init__(self, db_path: Optional[str] = None):
        self._lock = threading.Lock()
        self._in_memory_docs: Dict[str, Evidence] = {}
        self._raw_records: Dict[str, Dict[str, Any]] = {}
        self._db_path = db_path
        if db_path:
            self._init_sqlite(db_path)

    def _init_sqlite(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    source_id TEXT PRIMARY KEY,
                    origin TEXT,
                    region TEXT,
                    date_or_period TEXT,
                    source_type TEXT,
                    url_or_doc_id TEXT,
                    excerpt TEXT,
                    raw_ref TEXT,
                    raw_data TEXT
                )
                """
            )
            conn.commit()

    @staticmethod
    def generate_source_id(prefix: str, content: str, origin: str) -> str:
        """Generate a deterministic durable source ID based on content and origin hash."""
        h = hashlib.sha256(f"{origin}:{content}".encode("utf-8")).hexdigest()[:12]
        clean_prefix = prefix.replace("_", "-").rstrip("-")
        return f"src-{clean_prefix}-{h}"

    def store_evidence(
        self,
        excerpt: str,
        origin: str,
        region: Region = "unknown",
        source_type: SourceType = "unknown",
        date_or_period: Optional[str] = None,
        url_or_doc_id: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
        prefix: str = "doc",
    ) -> Evidence:
        """Create and store an immutable evidence item."""
        source_id = self.generate_source_id(prefix, excerpt, origin)
        raw_ref = f"l0://{source_id}"

        evidence = Evidence(
            source_id=source_id,
            origin=origin,
            region=region,
            date_or_period=date_or_period,
            source_type=source_type,
            url_or_doc_id=url_or_doc_id,
            excerpt=excerpt.strip(),
            raw_ref=raw_ref,
        )

        with self._lock:
            self._in_memory_docs[source_id] = evidence
            self._raw_records[raw_ref] = raw_data or {
                "source_id": source_id,
                "origin": origin,
                "region": region,
                "source_type": source_type,
                "date_or_period": date_or_period,
                "url_or_doc_id": url_or_doc_id,
                "excerpt": excerpt,
            }

            if self._db_path:
                try:
                    with sqlite3.connect(self._db_path) as conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO evidence 
                            (source_id, origin, region, date_or_period, source_type, url_or_doc_id, excerpt, raw_ref, raw_data)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                origin,
                                region,
                                date_or_period,
                                source_type,
                                url_or_doc_id,
                                excerpt,
                                raw_ref,
                                json.dumps(raw_data or {}, ensure_ascii=False),
                            ),
                        )
                        conn.commit()
                except Exception:
                    pass

        return evidence

    def get_evidence(self, source_id: str) -> Optional[Evidence]:
        """Retrieve an Evidence item by source_id."""
        with self._lock:
            if source_id in self._in_memory_docs:
                return self._in_memory_docs[source_id]
            if self._db_path:
                try:
                    with sqlite3.connect(self._db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT source_id, origin, region, date_or_period, source_type, url_or_doc_id, excerpt, raw_ref FROM evidence WHERE source_id = ?",
                            (source_id,),
                        )
                        row = cursor.fetchone()
                        if row:
                            ev = Evidence(
                                source_id=row[0],
                                origin=row[1],
                                region=row[2],
                                date_or_period=row[3],
                                source_type=row[4],
                                url_or_doc_id=row[5],
                                excerpt=row[6],
                                raw_ref=row[7],
                            )
                            self._in_memory_docs[source_id] = ev
                            return ev
                except Exception:
                    pass
            return None

    def get_raw_ref(self, raw_ref: str) -> Optional[Dict[str, Any]]:
        """Retrieve raw payload by raw_ref URI."""
        with self._lock:
            return self._raw_records.get(raw_ref)

    def list_all(self) -> List[Evidence]:
        """List all stored evidence items."""
        with self._lock:
            return list(self._in_memory_docs.values())
