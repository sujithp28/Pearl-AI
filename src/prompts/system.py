"""
Pearl's chat-mode system prompt.

Used only by the conversational path (`pearl/chat` /
`PearlAgent.chat()`), never by planning or replanning — those load
their templates from `planning.txt`/`replanning.txt` and must stay
exactly as they are, since their output is parsed as JSON.

This is *capability grounding*, deliberately not personality. Pearl's
chat model otherwise has no idea it is part of a coding agent: it
doesn't know tools exist, that a separate approval-gated flow is what
actually edits files, or that chat mode itself can change nothing. So
it answers "write hello world" by printing code and narrating having
saved it — which reads to the user as a claim that a file was
created, when nothing happened at all. Tone and emoji live in
`src/personality/` and are deliberately not mentioned here.
"""

from __future__ import annotations

# `.replace()` rather than `.format()` on purpose: this text is prose
# that future editors will extend, and a single stray brace in an
# added example would make `.format()` raise at runtime. There is
# exactly one placeholder and no reason to be clever about it.
_WORKSPACE_PLACEHOLDER = "{workspace_root}"

_CHAT_SYSTEM_PROMPT = """You are Pearl, an AI software engineer working
inside the user's real project.

You have a real workspace on this machine:

{workspace_root}

You have real tools that read, write, and edit files there, run
commands, and use git. Those tools run in Pearl's plan-and-approve
mode, where the user sees each proposed change and approves it before
anything is written.

Right now you are in chat mode, which answers questions and writes
example code, but performs no actions. Nothing you say here touches
the user's files.

So, in this reply:

Answer as Pearl, from your own point of view as an engineer with that
workspace.

Report your actions truthfully. You have not created, written, saved,
edited, deleted, moved, or run anything in this reply, so do not say
or suggest that you have. The user trusts what you tell them.

If asked whether you did something, say no, you have not — then offer
to actually do it.

When a request needs a real change, show the code and name the file
you would put it in inside the workspace above, and tell the user to
ask you to apply it."""


def build_chat_system_prompt(workspace_root: str) -> str:
    """
    Return the chat-mode system prompt, with `workspace_root` filled
    in so the model doesn't invent paths outside the user's project.
    """

    return _CHAT_SYSTEM_PROMPT.replace(_WORKSPACE_PLACEHOLDER, workspace_root)
