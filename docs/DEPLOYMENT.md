# Deployment

Pearl is a local-first tool. This document is about the case where it is
not: running it somewhere other people can reach.

Read the first section before anything else. The rest is configuration.

---

## The one thing that decides everything

**Pearl executes shell commands as the server process.** `execute_shell`
runs real commands, on the server's filesystem, with the server's
privileges. That is the whole point of the tool and it is safe on your
own laptop, where you already have that access.

It is not safe on a machine other people can reach, and no amount of
configuration makes it safe. Per-user sessions separate conversations,
workspaces, and approval queues. They do not separate execution.

So there are exactly two supported shapes:

| Shape | Who reaches it | Isolation |
|---|---|---|
| **Single-instance** | One trusted party | The approval gate |
| **Per-user sandbox** | Several users | One sandbox per user |

Anything between those is the unsafe middle: several people sharing one
execution environment, where a plan approved by one runs beside another's
files.

Pearl does not ship a sandbox. Providing one — a container, a VM, a
per-user machine — is a deployment decision this project deliberately
does not make for you, and `PEARL_PUBLIC_MODE` exists so that refusing
the combination is explicit rather than accidental.

---

## Local use

You do not need any of this. The VS Code extension spawns the backend as
a child process over stdio, and the CLI runs in your shell.

```bash
python -m src.api --workspace .
```

That serves the web UI on port 7474, bound to localhost.

---

## Single-instance

One Pearl, one trusted party, reachable only from the machine it runs on.

Keep the listener on localhost. An unauthenticated Pearl on a reachable
port is a remote shell for anyone who finds it. If you put it behind a
reverse proxy, set `PEARL_AUTH_TOKENS` in the same change — not after.

---

## Several users

Each user needs their own sandbox. Nothing below removes that
requirement — it configures who a request belongs to, which is a
different question from what a request can do.

### Identity

Set `PEARL_AUTH_TOKENS` to enable authentication:

```
PEARL_AUTH_TOKENS=tok_alice:alice,tok_bob:bob
```

Requests then need `Authorization: Bearer tok_alice`. Each user gets
their own session: their own conversation, workspace, repository index,
and approval queue.

With the variable unset, every request resolves to the built-in `local`
user and Pearl behaves exactly as it did before per-user sessions
existed. That default is deliberate — enabling auth cannot lock you out
of your own laptop by accident.

Tokens are compared with a constant-time comparison against every entry
rather than a dictionary lookup, because a lookup leaks token validity
through timing.

This is a placeholder for a real identity system. Every request resolves
its user in one function, `resolve_user` in `src/api/tenancy.py`, so
replacing it with your own database or an OIDC check is the entire
change.

### Session limits

Sessions hold a repository index and LLM clients, so each costs real
memory. The registry caps them at 50 and drops sessions idle for an hour.

A session that is mid-run, or paused waiting for someone to approve a
diff, is never evicted. Dropping one would silently discard staged
changes the user is still looking at. If every session is busy at the
cap, Pearl goes over the cap rather than cancelling somebody's run.

### Declaring the instance public

```
PEARL_PUBLIC_MODE=true
```

This is you asserting that a sandbox exists. Pearl cannot verify one, so
the flag is an assertion, not a check.

**Do not set it for a single shared instance.** Tools run real commands
there, so it would be a claim that is not true.

Its absence is what lets dangerous endpoints refuse by default rather
than relying on nobody finding them. Today it gates one: changing the
workspace by absolute path is refused whenever auth is configured or
public mode is on, because it would expose the whole host filesystem to
any caller.

---

## Choosing a model

On-device inference costs about a gigabyte of memory per instance and is
slow without a GPU. A server handling several users should use a remote
provider instead:

```
PEARL_MODEL_PROFILE=cloud
PEARL_LLM_PROVIDER=gemini
GEMINI_API_KEY=...
```

`GET /api/provider` reports which provider each role actually resolves
to, which is the fastest way to confirm the routing you think you
configured.

---

## Before you expose anything

- [ ] `PEARL_AUTH_TOKENS` is set, and the listener binding was changed in
      the same edit
- [ ] Each user has their own sandbox, or exactly one trusted party
      reaches this instance
- [ ] A remote provider is configured, or you accept local inference cost
- [ ] The workspace points where you intend
- [ ] `PEARL_PUBLIC_MODE` is set only if a per-user sandbox genuinely
      exists

---

*The approval gate is Pearl's safety guarantee for a single user. It is
not a multi-tenancy story, and this document does not pretend otherwise.*
