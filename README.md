# The Drone With a Nervous System

**Three small devices, one tiny model each, and a few bytes between them are enough to let a room full of people fly a drone without a data centre**

Scaffolded by `_demo-kit/new-demo.py` on 2026-11-04. Before building anything,
pick the use-case archetype in `../_demo-kit/PATTERNS.md` and read
`../_demo-kit/DESIGN_SYSTEM.md` — new components are built on the showcase
first, never here.

```bash
just up        # dashboard on :8020
just check     # tests + video-strict lint
just record-check   # everything that must be true before a take
```

Models: ask the kit's model gateway (`just gateway-up`, POST /ask on
127.0.0.1:18020), never a provider API. It replays `fixtures/model/` by
default; see `../_demo-kit/README.md`, Models.

Cloud workspace: `just workspace-init` installs the shared `expanso-demos`
profile and enrolls this demo's project-local edge identity. Use
`just workspace-check` for a read-only verification.
