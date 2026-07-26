# Pearl Dogfooding Log

Every time Pearl is used to develop Pearl, record the outcome here.
Failures are more valuable than successes.

**Rule:** If Pearl was used today, it gets a log entry.

---

## Status Levels

| Status | Meaning |
|---|---|
| 🟢 Success | Approved with no manual edits |
| 🟡 Near Miss | Needed manual edits before approval |
| 🔴 Failure | Could not complete the task |
| ⚪ Blocked | Missing capability — Pearl cannot attempt this yet |

Only 🟢 counts as a completed task. 🟡 is the most valuable entry: it means
Pearl almost worked, and the gap is specific and actionable.

---

## Weekly Summary

| Week | 🟢 Success | 🟡 Near Miss | 🔴 Failure | ⚪ Blocked |
|---|---|---|---|---|
| — | — | — | — | — |

---

## Log

---

### Session 001 — Dogfooding Sprint Day 1 (Easy Tasks 01–10)

**Date:** 2026-07-26  
**Tasks:** #01–10 (Easy tier)  
**Engineer:** Claude Code acting as Pearl lead

| # | Task | Status | Notes |
|---|---|---|---|
| 01 | Verify prompt template formatting | 🟢 | PASS — id/depends_on schema correct |
| 02 | PatchManager.is_empty | 🟢 | Added + 5 tests |
| 03 | ToolCall.__repr__ | 🟢 | Truncates long values, shows step_id |
| 04 | ToolRegistry.tool_names | ⚪ | Pre-existing as list_tools() |
| 05 | ToolRegistry.__contains__ | ⚪ | Pre-existing implementation |
| 06 | ExecutionReport.step_count | 🟢 | Added + tests |
| 07 | ExecutionReport.succeeded/failed_steps | 🟢 | Added + tests |
| 08 | PatchManager.__len__ | 🟡 | Added, but caused P1 bug (see below) |
| 09 | ToolCall.to_dict() | 🟢 | Added + tests, omits empty fields |
| 10 | ExecutionStep.__repr__ | 🟢 | Added + tests |
| 24 | ExecutionReport.replans_used | ⚪ | Pre-existing field |

**P1 Bug Discovered and Fixed:**

> `PatchManager.__len__` made empty instances falsy. `AutonomousExecutor.__init__`
> used `patch_manager or PatchManager()` which silently replaced a caller's empty
> PatchManager with a fresh one. The test `test_shared_patch_manager_can_be_passed_in`
> caught this correctly. Fixed by: (1) adding `__bool__ = True` to PatchManager and
> (2) switching `__init__` to explicit `is None` guards. Regression test added.

**P2 Documentation Bug:**

> `CLAUDE.md` Section 3 shows `assert "test.py" in pm.staged_paths` but
> `staged_paths` does not exist. Real API: `pm.affected_files()` or `pm.has_pending()`.

**Lessons learned:**

- Adding `__len__` to a collection-like class requires `__bool__` too, otherwise
  Python derives falsy behavior from zero length, breaking `or`-style default patterns.
- 3 of 10 backlog tasks were already implemented. Pre-flight indexing of existing
  capabilities would save redundant task entries in future sprints.
- The test suite caught the P1 bug immediately — approval invariant architecture is working.

**Tests added:** +13 (986 total, up from 973)

**Commit:** `9d0560c`

---

### Task #001

**Date:** _(fill in)_  
**Goal:** _(what you asked Pearl to do)_  
**Status:** 🟢 / 🟡 / 🔴 / ⚪

**Manual intervention:** Yes / No  
**Reason:** _(if yes, what did you change and why)_

**Pain points:**
- _(specific friction encountered)_

**Improvement ideas:**
- _(what would have made this work)_

**Created issues:**
- _(link or number — or "none")_

**Action taken:**
- [ ] Ignore
- [ ] Bug filed
- [ ] New feature
- [ ] Architecture change
- [ ] Prompt improvement
- [ ] Tool improvement
- [ ] Documentation update

---
