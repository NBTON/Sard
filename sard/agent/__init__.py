"""Agent/orchestration layer.

Currently home to `ChatService`, a thin provider-neutral service used by the
UI. In a later step this layer grows into a LangGraph pipeline (intent
detection -> retrieval -> grounding -> itinerary structuring -> export), with
`ChatService`'s responsibilities absorbed into one or more graph nodes.
"""
