"""Callable tools for the ten specialist agents.

Every module in here implements *real* logic. Capabilities that the brief
listed as tools but which are really prompt-engineered reasoning steps are not
here at all — they are declared as ``reasoning_tools`` in
:mod:`app.agents.registry`, with a note on each saying why.

Each module opens with a short block recording, per tool, what is real about
it and what it depends on. Read those before changing a tool's contract: the
agents' system prompts are written against these behaviours.
"""
