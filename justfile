set shell := ["bash", "-euo", "pipefail", "-c"]

port := `uv run --no-project scripts/demo-ports.py resolve --demo-dir . --allow-bound --format json | jq -r .PORT`
cloud := "uv run --no-project scripts/cloud_env.py --"
nodes := "hear-clap hear-commands see-hands fly-drone"

_default:
    @just --list

# Broker, three sense devices, flight service, screen, four Expanso Edge
# agents. The four jobs are deployed through Expanso Cloud and left stopped.
up:
    uv run python -m ri.stack up

# Stop every process, free every port, delete the four Cloud jobs.
down:
    uv run python -m ri.stack down

status:
    uv run python -m ri.stack status

# Drive the five-minute arc against a rig that is already up.
run:
    uv run python -m ri.conductor

# Record the whole run headless into recordings/, then check every beat.
record-sim: up
    uv run python -m ri.record || { just down; exit 1; }
    just down

# Enrol the four nodes into the shared Expanso Cloud workspace. Identities
# land in ./.expanso-edge/<node> (ignored, owner-only); no ~/.expanso.
workspace-init:
    @git check-ignore -q .expanso-edge/ && git check-ignore -q .expanso-cli/ || { echo "FAIL: .expanso-edge/ and .expanso-cli/ must be ignored"; exit 1; }
    @mkdir -p .expanso-edge && chmod 700 .expanso-edge
    @for n in {{nodes}}; do \
      if [ -f ".expanso-edge/$n/auth/credentials.creds" ]; then echo "$n: existing identity"; \
      else uv run --no-project scripts/cloud_env.py --with-bootstrap -- expanso-edge bootstrap --config "edge/$n.yaml" --data-dir ".expanso-edge/$n" > /dev/null && echo "$n: enrolled"; fi; \
    done

workspace-check:
    @{{cloud}} expanso-cli node list --label demo=demo-ri-nervous-system

# Camera fixtures: openly licensed people photos, cut into tiles at build
# time into fixtures/build/ (never committed). Needs network once.
fixtures:
    uv run python -m scripts.build_fixtures

# Retrain the keyword model from Speech Commands (CC BY 4.0, ~2.3 GB cache).
kws-train data="~/.cache/demo-ri-nervous-system/sc":
    uv run python -m scripts.train_kws --data {{data}}

# Delete the Speech Commands download once the model is trained.
kws-clean:
    rm -rf ~/.cache/demo-ri-nervous-system

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

test:
    uv run pytest -q

video-check:
    @uv run -s ../_demo-kit/lint-demo-ui.py . --video-strict

# the board must MOVE at zero state (rule 1 + flow grammar): two real screenshots, pixel diff
motion-check:
    @uv run -s ../_demo-kit/probe-motion.py .

# Review the running dashboard before showing it.
review-shots:
    @uv run -s ../_demo-kit/review-shots.py . http://127.0.0.1:{{port}}

# Expanso pipelines: syntax, then the all-demos rules (logs, a real output,
# short lines, no blank lines in config, generate only for timers).
validate:
    @command -v expanso-edge > /dev/null || { echo "FAIL: expanso-edge not installed"; exit 1; }
    expanso-edge validate pipelines/*.yaml

pipeline-check:
    @uv run -s ../_demo-kit/lint-demo-pipelines.py .

# fails if any file calls a model provider directly
provider-check:
    @uv run -s ../_demo-kit/lint-demo-providers.py .

check: lint test validate pipeline-check provider-check video-check motion-check clean-check

# everything that must be true before a take: gates + live endpoint + checklist
record-check: check
    curl -fsS "http://localhost:{{port}}/api/state" > /dev/null || { echo "FAIL: screen not reachable; just up first"; exit 1; }

# human story/proof declaration; validates only and never starts anything
recording-preflight:
    @uv run -s ../_demo-kit/recording-preflight.py .

# prohibited names never reach a take (add customer/competitor names here)
clean-check:
    @if rg -il '[b]acalhau' --glob '!justfile' . ; then echo "FAIL: prohibited name in tree"; exit 1; fi
    @just audience-words dashboard ri/conductor.py
    @echo "clean"

# Words the audience must never read on the screen or in a prompt line.
audience-words +paths:
    @if rg -n -i '\b(simulat\w*|illustrative|mock\w*|replay\w*|pre-?recorded|fake|live demo)\b' {{paths}}; then echo "FAIL: audience-facing word above"; exit 1; fi
