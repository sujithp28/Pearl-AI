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
| **Per-user sandbox** | Several users | One container per user |

Anything between those is the unsafe middle: several people sharing one
execution environment, where a plan approved by one runs beside another's
files.

---

## Local development

You do not need any of this. The VS Code extension spawns the backend as
a child process over stdio, and the CLI runs in your shell. Neither
involves a container, and running one for local work only adds latency
and takes the local model away from your GPU.

```bash
python -m src.api --workspace .
```

---

## Single-instance

One Pearl, one trusted party, reachable only from the machine it runs on.

```bash
docker compose up --build
```

Then open `http://localhost:7474`.

Before the first run, create the directory the compose file mounts:

```bash
mkdir -p workspace
```

Docker creates a missing bind source itself, but it creates it owned by
root, and the container runs as an unprivileged user. Making it yourself
avoids a permission failure on first start.

### What the compose file already decides for you

**The port is bound to `127.0.0.1`, not `0.0.0.0`.** An unauthenticated
Pearl on a reachable port is a remote shell for anyone who finds it. Do
not change this line without also setting `PEARL_AUTH_TOKENS`.

**Memory is capped at 4 GB.** A runaway plan cannot take the host with
it. Raise it if you run the local model and a large index together.

**Models and checkpoints are named volumes.** Without them, every
`up --build` re-downloads about 1.1 GB, and your undo history does not
survive a restart.

### Use a remote model here

A container is a poor place for on-device inference: slow without GPU
passthrough, and about a gigabyte of memory per instance. Uncomment one
of the provider blocks in `docker-compose.yml`, or set
`PEARL_MODEL_PROFILE=cloud`.

---

## Several users

Each user needs their own container. Nothing below removes that
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
its user in one function, `resolve_user`, so replacing it with your own
database or an OIDC check is the entire change.

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

**Do not set it for a single shared container.** Tools run real commands
there, so it would be a claim that is not true.

Its absence is what lets dangerous endpoints refuse by default rather
than relying on nobody finding them. Today it gates one: changing the
workspace by absolute path is refused whenever auth is configured or
public mode is on, because it would expose the whole host filesystem to
any caller.

---

## What the image does and does not do

**Does not bake in a model.** The default GGUF is about 1.1 GB and
changes independently of the code. Mount it, or let Pearl download it on
first run.

**Does not run as root.** Pearl executes shell commands as the server
process, so root here would mean every tool call is root inside the
container.

**Ships git.** Checkpoints and post-apply verification shell out to it.
An image without it loses undo and verification silently.

**Health-checks `/api/status`, not `/`.** The UI is a static file and
would report healthy with the session unusable.

---

## Before you expose anything

- [ ] `PEARL_AUTH_TOKENS` is set, and the port binding was changed in the
      same edit
- [ ] Each user has their own container, or exactly one trusted party
      reaches this one
- [ ] A remote provider is configured, or you accept local inference cost
- [ ] The workspace mount points where you intend, and exists
- [ ] `PEARL_PUBLIC_MODE` is set only if a per-user sandbox genuinely
      exists

---

*The approval gate is Pearl's safety guarantee for a single user. It is
not a multi-tenancy story, and this document does not pretend otherwise.*
