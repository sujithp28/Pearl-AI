# Pearl Dogfooding Session Template

Copy this file to start a new dogfooding log entry.
Fill in every section — blank fields are a signal that the session
was not reviewed critically enough to be useful.

---

## Session NNN — _Short description_

**Date:** YYYY-MM-DD  
**Tasks:** #XX–YY (_difficulty tier_)  
**Engineer:** _name_

| # | Task | Status | Notes |
|---|---|---|---|
| XX | _description_ | 🟢 / 🟡 / 🔴 / ⚪ | _one-liner_ |

---

### Bugs Discovered

#### _Bug title_ (P0 / P1 / P2)

> _One paragraph describing what went wrong, when it was triggered,
> and what the user-visible symptom was._

**Root cause:** _The underlying code defect._

**Fix:** _What was changed, in which file(s)._

**Regression test:** _test file and class/function name._

---

### Pre-existing Implementations Found

List any backlog tasks that turned out to already be implemented before
the sprint began. These should be marked ⚪ in the task table above.

- Task #XX — _description_ — pre-existing as _symbol/method name_

---

### Lessons Learned

- _Concrete, actionable insight from this session._
- _Not generic advice — something specific to Pearl's design or workflow._

---

### Metrics

**Tests added:** +N (from M total before this session)  
**Commits:** _list commit hashes_  
**P0 bugs:** N  
**P1 bugs:** N  
**P2 bugs (deferred):** N

---

## Individual Task Log

Use one entry per task below.

---

### Task #XX — _Task title_

**Expected behavior:** _What the task was supposed to accomplish._

**Actual behavior:** _What happened when you ran it._

**Status:** 🟢 Success / 🟡 Near Miss / 🔴 Failure / ⚪ Blocked / ⚪ Pre-existing

**Bugs discovered:**
- _None, or describe each defect found while doing this task._

**Fixes applied:**
- _None, or describe each fix and its test._

**Tests added:**
- _None, or list each test class/function and what it covers._

**Manual edits required:** Yes / No  
**If yes:** _What did you have to fix manually and why couldn't Pearl do it?_

**Improvement ideas:**
- _Specific changes to prompts, tools, or architecture that would
  have made this task go more smoothly._

---
