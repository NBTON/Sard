"""Provider-neutral chat service — the boundary between the UI and models.

The Streamlit UI (and, later, any other UI) never imports LangChain provider
integrations or ``sard.config.models`` directly. It only calls
:class:`ChatService`. This keeps the door open to replace the internals with
a LangGraph node/pipeline in a later step without touching the UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from sard.config.models import ModelConfigError, get_chat_model, get_model_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "أنت مساعد \"سرد\"، مساعد رحلات يجيب دائمًا باللغة العربية الفصحى "
    "بوضوح وإيجاز ودقة."
)


@dataclass(frozen=True)
class ChatResult:
    """Provider-agnostic result returned to the UI layer.

    Only ``ok``, ``text``, and ``error_message`` are exposed — never a raw
    provider response object or field.
    """

    ok: bool
    text: str = ""
    error_message: str = ""


class ChatService:
    """Provider-neutral chat service used by the UI layer.

    A LangChain chat model can be injected directly (used by tests to avoid
    any network access or API key). When omitted, the service lazily builds
    one from environment configuration via the central model factory on each
    call, so switching ``MODEL_PROVIDER``/``MODEL_NAME`` takes effect without
    restarting long-lived state.
    """

    def __init__(self, chat_model: Optional[BaseChatModel] = None):
        self._injected_model = chat_model

    def _get_model(self) -> BaseChatModel:
        if self._injected_model is not None:
            return self._injected_model
        return get_chat_model()

    def ask(self, user_query: str) -> ChatResult:
        """Send a user query to the configured chat model and return the reply.

        Never raises — configuration errors and unexpected failures are
        captured and returned as a sanitized :class:`ChatResult`.
        """
        if not user_query or not user_query.strip():
            return ChatResult(
                ok=False,
                error_message="الرجاء إدخال سؤال قبل الإرسال.",
            )

        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            return ChatResult(ok=False, error_message=str(exc))

        try:
            response = model.invoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(content=user_query),
                ]
            )
        except Exception:
            logger.exception("Unexpected error while invoking the chat model")
            return ChatResult(
                ok=False,
                error_message=(
                    "حدث خطأ غير متوقع أثناء الاتصال بنموذج الدردشة. "
                    "الرجاء المحاولة مرة أخرى لاحقًا."
                ),
            )

        text = getattr(response, "content", "")
        if not isinstance(text, str):
            text = str(text)

        return ChatResult(ok=True, text=text)


def current_status_label() -> str:
    """Human-readable "<provider> / <model>" label for the UI status area.

    Never raises; returns a friendly placeholder if configuration is
    missing or invalid so the UI can render a status area unconditionally.
    """
    try:
        settings = get_model_settings()
        return f"{settings.provider} / {settings.model_name}"
    except ModelConfigError:
        return "غير مُعدّ بعد (not configured)"
