set shell := ["bash", "-euo", "pipefail", "-c"]

port := `uv run --no-project scripts/demo-ports.py resolve --demo-dir . --allow-bound --format json | jq -r .PORT`
cloud := "uv run --no-project scripts/cloud_env.py --"
nodes := "hear-clap hear-commands see-hands fly-drone"
default_drone := "codrone"
default_cam := "AnkerWork C310|OBSBOT Tiny 2 Lite"
default_mic := "mac"

_default:
    @just --list

# List connected microphone and camera names/indices.
cameras:
    uv run python -m ri.capture

# Default live rig: CoDrone EDU + the MacBook mic + the first camera in default_cam that is plugged in.
# `just up small` sizes the flight for a hotel room, `just up large` for the stage,
# `just up hall` for a big hall;
# with no size it keeps the last one used. Override the rest with parameters
# (for example `just up large sim fixture none`).
up size="" drone=default_drone input="live" cam=default_cam mic=default_mic hands_input="":
    uv run python -m ri.stack up --room "{{size}}" --drone {{drone}} --input {{input}} --clap-device "{{mic}}" --word-device "{{mic}}" --stage-cam "{{cam}}" --hands-device "{{cam}}" --hands-input "{{ if hands_input != '' { hands_input } else { input } }}"

# Fixture/simulator override when running without physical hardware.
up-sim:
    just up "" sim fixture none

# Live CoDrone + live OBSBOT Tiny 2 Lite camera with fixture audio cues.
up-cam cam=default_cam drone=default_drone:
    just up "" {{drone}} fixture "{{cam}}" "{{default_mic}}" live

# Alias for `just up` (CoDrone + OBSBOT Tiny 2 Lite + the MacBook mic).
up-live drone=default_drone cam=default_cam mic=default_mic:
    just up "" {{drone}} live "{{cam}}" "{{mic}}"

# Launch the rig AND immediately start all four Expanso Cloud pipelines.
live drone=default_drone cam=default_cam mic=default_mic:
    just up "" {{drone}} live "{{cam}}" "{{mic}}"
    just start

# Default hardware preflight: CoDrone EDU + OBSBOT Tiny 2 Lite + DJI/Mac audio.
preflight drone=default_drone input="live" cam=default_cam:
    uv run python -m ri.preflight --drone {{drone}} --input {{input}} --stage-cam "{{cam}}" --hands-device "{{cam}}" --hands-input "{{input}}"

# Preflight against simulator and fixtures (no physical hardware needed).
preflight-sim:
    uv run python -m ri.preflight --drone sim --input fixture --stage-cam none --hands-device "" --hands-input fixture

# Alias for `just preflight`.
preflight-cam drone=default_drone cam=default_cam input="live":
    just preflight {{drone}} {{input}} "{{cam}}"

# Is the running rig ready to fly? One line per thing that can let you down.
ready wait="0":
    uv run python -m ri.ready --wait {{wait}}

# Fast clean restart between run-throughs: same jobs in Expanso Cloud, fresh
# processes on the current code, then the readiness check.
again size="" drone=default_drone cam=default_cam mic=default_mic:
    uv run python -m ri.stack down --keep-jobs
    uv run python -m ri.stack up --room "{{size}}" --skip-deploy --drone {{drone}} --input live --clap-device "{{mic}}" --word-device "{{mic}}" --stage-cam "{{cam}}" --hands-device "{{cam}}" --hands-input live
    just ready 45

# The last flight from the recorder, as a timeline: what was asked, how long
# each move took to start and finish, heights reached, both height sensors.
# `just flights 2` is the one before.
flights nth="1":
    uv run python -m ri.flightlog show {{nth}}

# Live readout of what the camera reads off your arms, twice a second, to
# tune the left, right and jazz-hands signals. Needs the rig up.
# `just arms --save` also keeps each frame, arms drawn, in .runtime/arms/.
arms *flags:
    uv run python -m ri.arms {{flags}}

# Zero the drone's gyro: drone still, on a hard, level table. Stops the rig
# first (the Cloud jobs are kept); `just again` brings it back.
level:
    uv run python -m ri.stack down --keep-jobs
    uv run python -m ri.level

# Stop every process, free every port, delete the four Cloud jobs.
down:
    uv run python -m ri.stack down

status:
    uv run python -m ri.stack status

# Start the four deployed Expanso Cloud jobs (`4/4 running`) for live flight.
start:
    uv run python -m ri.cloud start

# Stop the four Expanso Cloud jobs back to `0/4` without shutting down services.
stop-jobs:
    uv run python -m ri.cloud stop

# Drive the five-minute arc against a rig that is already up.
run:
    uv run python -m ri.conductor

# Record the whole run headless on sim + fixtures into recordings/, then check every beat.
record-sim: up-sim
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

# What the demo is using on disk, against its 10 GB budget.
disk:
    uv run python -m ri.disk

# Delete rebuildable files (tool caches, review shots, training download) if over budget.
disk-trim:
    uv run python -m ri.disk --trim

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

test:
    YOLO_CONFIG_DIR=.runtime/ultralytics uv run pytest -q

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
