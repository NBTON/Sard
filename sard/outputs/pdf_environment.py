"""Process-safe compatibility bridge for the legacy PDF root environment."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_PDF_ROOT_LOCK = threading.RLock()


@contextmanager
def locked_pdf_output_root(root: str | Path) -> Iterator[None]:
    """Serialize temporary PDF-root changes and restore the prior value."""

    with _PDF_ROOT_LOCK:
        previous = os.environ.get("SARD_PDF_OUTPUT_ROOT")
        os.environ["SARD_PDF_OUTPUT_ROOT"] = str(Path(root))
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("SARD_PDF_OUTPUT_ROOT", None)
            else:
                os.environ["SARD_PDF_OUTPUT_ROOT"] = previous
