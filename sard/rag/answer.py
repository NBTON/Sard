"""Grounded Arabic answer generation with lightweight citation verification.

Connects the retrieval/rerank output to a ChatNVIDIA model (via the central
factory in ``sard.config.rag``), using only the Step 2 LangChain chat-model
interface — never a provider SDK directly.

Full claim-level verification and graph retries are explicitly out of scope
for Step 3 (see the module docstring note in the task spec); this module
only guarantees the minimum bar: an answer is never allowed to keep a
citation ID that wasn't actually present in the packed context.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from sard.config.rag import RAGSettings, build_chat_model, get_rag_settings
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    FallbackEvent,
    ModelCandidate,
    run_with_fallback,
)
from sard.rag.schemas import AnswerResult, Citation, RetrievedCandidate

logger = logging.getLogger(__name__)

_CITATION_TOKEN_RE = re.compile(r"\[(CIT-[A-Za-z0-9_-]+)\]")

_SYSTEM_PROMPT_TEMPLATE = (
    "أنت مساعد \"سرد\" للإجابة المُسنَدة بالأدلة (grounded RAG). أجب بالعربية "
    "الفصحى الواضحة، واستخدم حصرًا المعلومات الواردة في السياق المرفق أدناه؛ "
    "لا تستخدم أي معرفة سابقة لديك كمصدر. إذا كانت الأدلة غير كافية للإجابة "
    "بثقة، صرّح بذلك بوضوح بدلاً من التخمين. حافظ على أي تفاوت أو تعارض بين "
    "المصادر بدلاً من حسمه تعسفًا.\n\n"
    "لكل ادعاء واقعي، أضف بعده مباشرة معرّف الاستشهاد بالضبط كما يظهر بين "
    "قوسين معقوفين، مثل [CIT-XXXXXXXXXXXX]. استخدم فقط معرّفات الاستشهاد "
    "الموجودة فعليًا في السياق أدناه؛ لا تخترع معرّفًا جديدًا أبدًا.\n\n"
    "السياق:\n{context}"
)


def pack_context(candidates: list[RetrievedCandidate]) -> str:
    """Format retrieved chunks into a numbered, citation-tagged context block."""
    blocks = []
    for c in candidates:
        location = f" (صفحة {c.page_number})" if c.page_number else ""
        blocks.append(
            f"[{c.citation_id}] {c.title} — {c.source_name}{location}\n{c.content}"
        )
    return "\n\n---\n\n".join(blocks)


def _repair_citations(text: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """Strip any citation token not present in the supplied context.

    Returns the repaired text and the list of valid citation IDs actually
    referenced, in order of first appearance.
    """
    referenced: list[str] = []
    seen = set()

    def _sub(match: re.Match) -> str:
        cid = match.group(1)
        if cid in valid_ids:
            if cid not in seen:
                referenced.append(cid)
                seen.add(cid)
            return match.group(0)
        return ""  # repair: drop fabricated citation tokens

    repaired = _CITATION_TOKEN_RE.sub(_sub, text)
    return repaired, referenced


class AnswerService:
    def __init__(
        self,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        chat_model_factory=build_chat_model,
    ):
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker or CircuitBreaker()
        self._chat_model_factory = chat_model_factory

    def _candidates(self) -> list[ModelCandidate]:
        route = self._settings.chat_route
        endpoint_type = "self_hosted" if self._settings.chat_base_url else "hosted"
        candidates = [
            ModelCandidate(model_id=route.primary, endpoint_type=endpoint_type, label="primary")
        ]
        for i, fb in enumerate(route.fallbacks, start=1):
            candidates.append(
                ModelCandidate(model_id=fb, endpoint_type=endpoint_type, label=f"fallback_{i}", degraded=True)
            )
        return candidates

    def _citations_from(self, candidates: list[RetrievedCandidate], ids: list[str]) -> list[Citation]:
        by_id = {c.citation_id: c for c in candidates}
        out = []
        for cid in ids:
            c = by_id.get(cid)
            if c is None:
                continue
            out.append(
                Citation(
                    citation_id=cid,
                    title=c.title,
                    source_name=c.source_name,
                    source_url=c.source_url,
                    chunk_id=c.chunk_id,
                )
            )
        return out

    def _extractive_fallback(
        self, question: str, candidates: list[RetrievedCandidate], reason: str
    ) -> AnswerResult:
        if not candidates:
            return AnswerResult(
                question=question,
                answer_text=(
                    "تعذّر توليد إجابة: لم يتم العثور على أدلة ذات صلة في المصادر "
                    "المتاحة حاليًا لهذا السؤال."
                ),
                citations=[],
                generation_mode="extractive_fallback",
                model_used=None,
                warnings=[reason, "لا توجد أدلة مسترجعة."],
            )

        lines = [
            "تعذّر توليد إجابة تركيبية تلقائيًا؛ فيما يلي ملخص مباشر من الأدلة "
            "المسترجعة مع الاستشهادات:",
            "",
        ]
        ids_in_order = []
        for c in candidates:
            lines.append(f"- {c.content.strip()} [{c.citation_id}]")
            ids_in_order.append(c.citation_id)
        text = "\n".join(lines)
        return AnswerResult(
            question=question,
            answer_text=text,
            citations=self._citations_from(candidates, ids_in_order),
            generation_mode="extractive_fallback",
            model_used=None,
            warnings=[reason],
        )

    def generate(self, question: str, candidates: list[RetrievedCandidate]) -> tuple[AnswerResult, list[FallbackEvent]]:
        top = [c for c in candidates[: self._settings.final_top_k] if getattr(c, "is_relevant", True)]
        if not top:
            return self._extractive_fallback(question, top, "لا توجد قطع مسترجعة ذات صلة لتغذية النموذج."), []

        valid_ids = {c.citation_id for c in top}
        context = pack_context(top)
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(context=context)
        nvidia_candidates = self._candidates()

        def call(candidate: ModelCandidate) -> AnswerResult:
            from langchain_core.messages import HumanMessage, SystemMessage

            model = self._chat_model_factory(candidate.model_id, self._settings)
            response = model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=question)]
            )
            content = getattr(response, "content", "")
            if not isinstance(content, str):
                content = str(content)
            if not content.strip():
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, "Generation model returned empty content."
                )

            repaired_text, referenced_ids = _repair_citations(content, valid_ids)
            warnings = []
            citation_tokens = _CITATION_TOKEN_RE.findall(content)
            if len(referenced_ids) < len(citation_tokens):
                warnings.append(
                    "تم حذف معرّفات استشهاد غير موجودة في السياق المسترجع من الإجابة."
                )
            if not referenced_ids:
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT,
                    "Generation model returned no valid citation for grounded context.",
                )

            return AnswerResult(
                question=question,
                answer_text=repaired_text,
                citations=self._citations_from(top, referenced_ids),
                generation_mode="generative",
                model_used=candidate.model_id,
                warnings=warnings,
            )

        try:
            result, events = run_with_fallback(
                "generation",
                nvidia_candidates,
                call,
                max_retries_per_candidate=max(1, self._settings.max_retries),
                circuit_breaker=self._breaker,
            )
            return result, events
        except AllCandidatesFailedError as exc:
            return (
                self._extractive_fallback(
                    question, top, "تعذّر الوصول إلى جميع نماذج التوليد المهيّأة."
                ),
                exc.events,
            )
