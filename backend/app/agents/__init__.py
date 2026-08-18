"""The Agentic Loop: ten purpose-built specialist agents.

This package is the fourth product surface, alongside Chat, Learn and Code. It
deliberately reuses rather than duplicates: the LLM router, the emitter, the
websocket event contract, the E2B sandbox, the Exa search client, the file
upload pipeline and the Learn section's source extractor are all the existing
ones. What is new here is a *parameterised* graph — one template, ten personas
and ten toolsets — plus the tools those personas need.

Read :mod:`app.agents.registry` first; it is the map of the whole feature, and
every tool records in its own module whether it is a real integration or a
prompt-engineered reasoning step.
"""
