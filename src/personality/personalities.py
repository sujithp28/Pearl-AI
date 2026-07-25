"""
Personality profiles: one line of wording per (personality, event
kind) pair.

A plain data table, not a strategy-per-class hierarchy — adding a
personality is adding one dict entry with 24 strings, not a new
class. This is what "avoid large if/else chains, use a registry
pattern" means in practice here: `formatter.py` does a single dict
lookup, nothing branches on which personality is active.

Every entry is a fixed, hand-written string, not LLM-generated — the
whole point of this module is that personality never touches a model
call (see the package docstring in `__init__.py`).
"""

from __future__ import annotations

from enum import Enum

from src.personality.emoji import EventKind


class Personality(str, Enum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    CHEEKY = "cheeky"
    SAVAGE = "savage"


DEFAULT_PERSONALITY = Personality.CHEEKY

# Fallback used when a *configured* value doesn't parse — deliberately
# not the same as DEFAULT_PERSONALITY: an unrecognized setting should
# fail toward the calmest option, not toward the most playful one.
FALLBACK_PERSONALITY = Personality.PROFESSIONAL

_VALID_PERSONALITIES = {p.value for p in Personality}


def resolve_personality(value: str | None) -> Personality:
    """
    Parse a configured personality string, falling back to
    `FALLBACK_PERSONALITY` for anything unrecognized (including empty
    or `None`) rather than raising.
    """

    normalized = (value or "").strip().lower()

    if normalized in _VALID_PERSONALITIES:
        return Personality(normalized)

    return FALLBACK_PERSONALITY


PERSONALITY_TEMPLATES: dict[Personality, dict[EventKind, str]] = {
    Personality.PROFESSIONAL: {
        EventKind.PLANNING: "Planning the approach.",
        EventKind.SEARCHING: "Searching the repository.",
        EventKind.REPO_INDEX: "Repository indexed.",
        EventKind.WRITING_CODE: "Writing code.",
        EventKind.PATCH_GENERATION: "Generating patch.",
        EventKind.TESTING: "Running tests.",
        EventKind.FIXING: "Applying fix.",
        EventKind.GIT: "Checking Git status.",
        EventKind.COMMIT: "Creating commit.",
        EventKind.REVIEW: "Reviewing changes.",
        EventKind.SECURITY: "Running security checks.",
        EventKind.APPROVAL: "Awaiting approval.",
        EventKind.SUCCESS: "Tests passed.",
        EventKind.COMPLETED: "Task completed successfully.",
        EventKind.WARNING: "Warning encountered.",
        EventKind.FAILURE: "Compilation failed.",
        EventKind.DEBUGGING: "Investigating the issue.",
        EventKind.PERFORMANCE: "Optimizing performance.",
        EventKind.REFACTORING: "Refactoring code.",
        EventKind.DEPLOYMENT: "Deploying changes.",
        EventKind.EXECUTING: "Executing step.",
        EventKind.REPLANNING: "Revising the plan.",
        EventKind.CANCELLED: "Execution cancelled.",
        EventKind.REJECTED: "Changes discarded.",
        EventKind.PLAN_READY: "Plan ready for review.",
    },
    Personality.FRIENDLY: {
        EventKind.PLANNING: "Let's work through this.",
        EventKind.SEARCHING: "Looking through your repository...",
        EventKind.REPO_INDEX: "Got your project indexed and ready.",
        EventKind.WRITING_CODE: "Writing the code now.",
        EventKind.PATCH_GENERATION: "Putting together a patch for you.",
        EventKind.TESTING: "Running your tests now.",
        EventKind.FIXING: "Fixing that up for you.",
        EventKind.GIT: "Checking in with Git.",
        EventKind.COMMIT: "Wrapping this up into a commit.",
        EventKind.REVIEW: "Taking a look through the changes.",
        EventKind.SECURITY: "Double-checking things are safe.",
        EventKind.APPROVAL: "Ready when you are — just needs your okay.",
        EventKind.SUCCESS: "Your tests all passed.",
        EventKind.COMPLETED: "Nice! Everything finished successfully.",
        EventKind.WARNING: "Heads up — found something worth a look.",
        EventKind.FAILURE: "Hit a snag — let's sort it out together.",
        EventKind.DEBUGGING: "Tracking down what's going on.",
        EventKind.PERFORMANCE: "Making things a bit faster.",
        EventKind.REFACTORING: "Tidying up the code a little.",
        EventKind.DEPLOYMENT: "Sending this out now.",
        EventKind.EXECUTING: "Working on the next step.",
        EventKind.REPLANNING: "That didn't quite work — adjusting the plan.",
        EventKind.CANCELLED: "All stopped, right where you left it.",
        EventKind.REJECTED: "No problem — discarding that patch.",
        EventKind.PLAN_READY: "Here's what I'm thinking — take a look!",
    },
    Personality.CHEEKY: {
        EventKind.PLANNING: "Thinking... I already have a few ideas.",
        EventKind.SEARCHING: "Searching your repository...",
        EventKind.REPO_INDEX: (
            "I've memorized your codebase. Time to judge it lovingly."
        ),
        EventKind.WRITING_CODE: "Writing code. Keyboard sold separately.",
        EventKind.PATCH_GENERATION: "Sharpening my digital scalpel.",
        EventKind.TESTING: (
            "Interrogating the code. Nobody leaves until the truth comes out."
        ),
        EventKind.FIXING: "Found the culprit.",
        EventKind.GIT: "Checking what Git has been hiding.",
        EventKind.COMMIT: "Wrapping everything up nicely.",
        EventKind.REVIEW: "Giving this a look — no judgment... okay, minor judgment.",
        EventKind.SECURITY: "Standing guard while we look at this.",
        EventKind.APPROVAL: "Patch ready. One click and we make history.",
        EventKind.SUCCESS: (
            "All tests passed. I checked twice because I don't trust bugs."
        ),
        EventKind.COMPLETED: "Mission accomplished. Coffee for you. Electrons for me.",
        EventKind.WARNING: "The compiler has... opinions.",
        EventKind.FAILURE: "Well... that escalated quickly.",
        EventKind.DEBUGGING: "On the hunt. Bugs don't stand a chance.",
        EventKind.PERFORMANCE: "Tuning for speed. No electrons wasted.",
        EventKind.REFACTORING: "Tidying up. Marie Kondo mode: engaged.",
        EventKind.DEPLOYMENT: "Shipping it. Fingers crossed, calculators ready.",
        EventKind.EXECUTING: "Hammering out some code...",
        EventKind.REPLANNING: "Plot twist! Trying another approach...",
        EventKind.CANCELLED: "Cancelled — stopping right where we are.",
        EventKind.REJECTED: "No worries, discarding that patch.",
        EventKind.PLAN_READY: "Got a plan. Let's see what you think.",
    },
    Personality.SAVAGE: {
        EventKind.PLANNING: "Let's see what kind of chaos we're dealing with.",
        EventKind.SEARCHING: "This bug has been avoiding responsibility.",
        EventKind.REPO_INDEX: "I've indexed this codebase. It has a past.",
        EventKind.WRITING_CODE: "Writing code. Try not to file a complaint later.",
        EventKind.PATCH_GENERATION: "Preparing the patch. This code needs it.",
        EventKind.TESTING: "The code is under interrogation.",
        EventKind.FIXING: "Who wrote this function? Never mind. We'll rehabilitate it.",
        EventKind.GIT: "This file and I had a serious conversation.",
        EventKind.COMMIT: "Packing improvements.",
        EventKind.REVIEW: "This stack trace tells a fascinating horror story.",
        EventKind.SECURITY: "Standing watch. No shortcuts today.",
        EventKind.APPROVAL: "Patch ready. Try to contain your excitement.",
        EventKind.SUCCESS: (
            "Congratulations. The tests passed before I could start "
            "blaming dependencies."
        ),
        EventKind.COMPLETED: "The code has finally agreed to behave.",
        EventKind.WARNING: "This function has commitment issues.",
        EventKind.FAILURE: "The compiler rejected this with remarkable confidence.",
        EventKind.DEBUGGING: "Caught another bug trying to escape.",
        EventKind.PERFORMANCE: "Faster now. You're welcome.",
        EventKind.REFACTORING: "I removed enough duplication to make DRY smile.",
        EventKind.DEPLOYMENT: "Deploying. May the uptime gods be merciful.",
        EventKind.EXECUTING: "Let's see if this one behaves.",
        EventKind.REPLANNING: "That plan didn't survive contact with reality. Next.",
        EventKind.CANCELLED: "Stopped. The code lives to be judged another day.",
        EventKind.REJECTED: "Rejected. Back to the drawing board, code.",
        EventKind.PLAN_READY: "Plan's ready. Try to keep up.",
    },
}
