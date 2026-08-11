"""Streamlit UI for the Sard MVP — Arabic RTL chat vertical slice + RAG mode.

This module handles presentation only. Chat logic lives behind
:class:`sard.agent.chat_service.ChatService`; RAG logic lives behind
:class:`sard.rag.service.RAGService` — the provider-independent RAG entry
point. This file NEVER imports Zvec, an NVIDIA integration, or
``sard.config.models`` / ``sard.config.rag`` directly.

Run with:

    uv run streamlit run sard/ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run sard/ui/app.py` to work even if the package isn't
# installed (editable or otherwise) in the current environment.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

from sard.agent.chat_service import ChatService, current_status_label  # noqa: E402
from sard.rag.service import RAGService, RAGServiceUnavailableError  # noqa: E402

st.set_page_config(page_title="سرد | Sard", page_icon="🧭", layout="centered")

_RTL_CSS = """
<style>
.stApp, .stApp * {
    direction: rtl;
}
.stChatMessage, .stMarkdown p, .stMarkdown li, textarea, input {
    text-align: right;
    font-family: "Segoe UI", Tahoma, Arial, sans-serif;
}
[data-testid="stChatInput"] textarea {
    text-align: right;
}
</style>
"""
st.markdown(_RTL_CSS, unsafe_allow_html=True)

st.title("سرد (Sard)")
st.caption("مساعد رحلات باللغة العربية — نموذج أولي (دردشة + إجابة مسندة بالأدلة RAG)")

with st.sidebar:
    st.subheader("الإعدادات الحالية")
    st.write("المزوّد والنموذج المُفعّلان حاليًا:")
    st.code(current_status_label(), language=None)
    st.caption(
        "لتغيير المزوّد أو النموذج، عدّل متغيّرات البيئة MODEL_PROVIDER و "
        "MODEL_NAME في ملف .env ثم أعد تشغيل التطبيق. لا حاجة لتعديل الكود."
    )
    use_rag = st.toggle(
        "وضع الإجابة المُسنَدة بالأدلة (RAG)",
        value=False,
        help="عند التفعيل، تُجاب الأسئلة من مجموعة الوثائق المفهرسة محليًا "
        "مع الاستشهادات (يتطلب فهرسة مسبقة عبر CLI: ingest).",
    )

if "chat_service" not in st.session_state:
    st.session_state.chat_service = ChatService()

if "messages" not in st.session_state:
    st.session_state.messages = []  # list[{"role": "user"|"assistant", "content": str}]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

user_query = st.chat_input("اكتب سؤالك هنا بالعربية...")


def _render_rag_answer(result) -> str:
    """Format a cited RAG answer for markdown display (UI presentation only)."""
    text = result.answer_text
    if result.citations:
        text += "\n\n**المصادر:**"
        for i, citation in enumerate(result.citations, start=1):
            text += (
                f"\n{i}. [{citation.title}]({citation.source_url}) — "
                f"{citation.source_name} (استشهاد {citation.citation_id})"
            )
    route_parts = []
    if result.model_route.get("generation"):
        route_parts.append(f"توليد: {result.model_route['generation']}")
    route_parts.append(f"استرجاع: {result.retrieval_mode}")
    if result.reranker_used:
        route_parts.append(f"إعادة ترتيب: {result.reranker_used}")
    text += "\n\n<details><summary>مسار النموذج والتفاصيل التقنية</summary>\n\n" + "\n\n".join(route_parts)
    if result.warnings:
        text += "\n\n" + "\n\n".join(f"- {w}" for w in result.warnings)
    text += "\n\n</details>"
    return text


if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("جارٍ التفكير..."):
            if use_rag:
                try:
                    rag_service = RAGService.open_readonly()
                    try:
                        rag_result = rag_service.answer(user_query)
                        answer_text = _render_rag_answer(rag_result)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": answer_text}
                        )
                    finally:
                        rag_service.close()
                    st.markdown(answer_text)
                except RAGServiceUnavailableError as exc:
                    st.warning(
                        f"{exc} — سيتم الرد عبر نموذج الدردشة المباشر بدلاً من ذلك."
                    )
                    result = st.session_state.chat_service.ask(user_query)
                    if result.ok:
                        st.markdown(result.text)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": result.text}
                        )
                    else:
                        st.error(result.error_message)
                except Exception:  # noqa: BLE001 - UI must never crash on RAG errors
                    st.warning(
                        "تعذّر تنفيذ الإجابة المسندة بالأدلة (خطأ غير متوقع في "
                        "خدمة الاسترجاع). سيتم الرد عبر نموذج الدردشة المباشر."
                    )
                    result = st.session_state.chat_service.ask(user_query)
                    if result.ok:
                        st.markdown(result.text)
                        st.session_state.messages.append(
                            {"role": "assistant", "content": result.text}
                        )
                    else:
                        st.error(result.error_message)
            else:
                result = st.session_state.chat_service.ask(user_query)
                if result.ok:
                    st.markdown(result.text)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": result.text}
                    )
                else:
                    st.error(result.error_message)
