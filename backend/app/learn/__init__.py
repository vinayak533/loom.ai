"""Learn — notebook sources, retrieval, grounded answers and lessons.

The Learn section is the one part of the app that reads documents rather than
running an agent. Nothing in this package touches the LangGraph loop or the
websocket contract: it is plain request/response over `app.api.learn`, and
every model call goes through the same `app.llm_router` as everything else.
"""
