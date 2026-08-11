"""Streamlit UI layer.

UI code must only talk to `sard.agent.chat_service.ChatService`. It must
never import a provider SDK or `sard.config.models` directly.
"""
