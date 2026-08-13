"""Deterministic UTF-8 rendering of the final verified Arabic answer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from sard.outputs.schemas import CitationSource, INLINE_CITATION_RE, VerificationStatus


MIME_TYPE = "text/plain; charset=utf-8"
_SECRET_RE = re.compile(r"(?i)(api[_-]?key|authorization|bearer|password|secret|credential)\s*[:=]\s*\S+")


@dataclass(frozen=True)
class RawTextResult:
    data: bytes
    warnings: tuple[str, ...] = ()

    def __bytes__(self) -> bytes:
        return self.data


def _safe_text(value: str) -> str:
    value = (value or "").replace("\x00", "").strip()
    return _SECRET_RE.sub(r"\1=[REDACTED]", value)


def render_raw_text(
    answer: str,
    sources: Iterable[CitationSource] = (),
    *,
    verification_status: VerificationStatus | str = VerificationStatus.VERIFIED,
    retrieval_mode: str = "",
    warnings: Iterable[str] = (),
    degraded_notice: Optional[str] = None,
) -> RawTextResult:
    status = getattr(verification_status, "value", verification_status)
    source_list = list(sources)
    source_ids = {source.citation_id for source in source_list}
    unknown = sorted(set(INLINE_CITATION_RE.findall(answer)) - source_ids)
    if unknown:
        raise ValueError(f"Unknown citation ID(s) in raw answer: {', '.join(unknown)}")
    lines = [_safe_text(answer) or "لم تتوفر إجابة عربية موثقة قابلة للحفظ.", ""]
    lines.append(f"حالة التحقق: {status}")
    if retrieval_mode:
        lines.append(f"وضع الاسترجاع: {_safe_text(retrieval_mode)}")
    if degraded_notice:
        lines.extend(["", f"تنبيه: {_safe_text(degraded_notice)}"])
    if source_list:
        lines.extend(["", "المصادر:"])
        for source in source_list:
            lines.append(f"[{source.citation_id}] {_safe_text(source.title)}")
            lines.append(_safe_text(source.url))
    warning_list = tuple(_safe_text(w) for w in warnings if str(w).strip())
    if warning_list:
        lines.extend(["", "تحذيرات:"])
        lines.extend(f"- {warning}" for warning in warning_list)
    text = "\n".join(lines).rstrip() + "\n"
    return RawTextResult(text.encode("utf-8"), warning_list)
