#!/usr/bin/env python3
"""Run one command with the shared Expanso Cloud workspace in its environment.

Only the Cloud recipes call this. The credentials stay in an owner-only .env
(this checkout's, gitignored, or the file RI_ENV_FILE names) and are read
at run time: never printed, logged or put on a command line. The child gets them
under the names the Expanso tools read, plus EXPANSO_CLI_CONFIG_DIR pointing
at ./.expanso-cli, so nothing reads or writes a profile under ~/.expanso.

    uv run --no-project scripts/cloud_env.py -- expanso-cli node list
    uv run --no-project scripts/cloud_env.py --with-bootstrap -- expanso-edge bootstrap ...
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI_CONFIG_DIR = ROOT / ".expanso-cli"

ENDPOINT = "EXPANSO_CLI_ENDPOINT"
API_KEY = "EXPANSO_CLI_API_KEY"
BOOTSTRAP = "EXPANSO_EDGE_BOOTSTRAP_TOKEN"
# .env name -> the process name the Expanso tools read. The CLI wants the key
# as EXPANSO_CLI_AUTH_API_KEY; the .env stores it as EXPANSO_CLI_API_KEY.
PROCESS_NAME = {
    ENDPOINT: ENDPOINT,
    API_KEY: "EXPANSO_CLI_AUTH_API_KEY",
    BOOTSTRAP: BOOTSTRAP,
}
# Inherited names that could steer a child somewhere else: a global profile, a
# stale key, another config dir. All are dropped before the file's values go in.
DROPPED = {
    *PROCESS_NAME.values(),
    API_KEY,
    "EXPANSO_CLI_PROFILE",
    "EXPANSO_CLI_CONFIG_DIR",
    "EXPANSO_EDGE_BOOTSTRAP_URL",
}

ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")


class EnvFileError(ValueError):
    """A problem with the credentials file. The message never contains a value."""


def default_env_file() -> Path:
    """RI_ENV_FILE if set, else this checkout's .env, else a sibling demos
    folder's shared _demo-kit/.env when this checkout lives among other demos."""
    named = os.environ.get("RI_ENV_FILE")
    if named:
        return Path(named).expanduser()
    local = ROOT / ".env"
    if local.is_file():
        return local
    shared = ROOT.parent / "_demo-kit" / ".env"
    if shared.is_file():
        return shared
    raise EnvFileError("no credentials file: copy env.example to .env or set RI_ENV_FILE")


def read_env_file(path: Path) -> str:
    """Return the file text, or raise EnvFileError if it is unsafe to trust."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise EnvFileError(f"{path} is missing") from None
    except PermissionError:
        raise EnvFileError(f"{path} is not readable by this user") from None
    except OSError as error:
        raise EnvFileError(f"{path} cannot be opened ({error.strerror})") from None
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise EnvFileError(f"{path} is not a regular file")
        if info.st_uid != os.geteuid():
            raise EnvFileError(f"{path} is not owned by this user")
        if info.st_mode & 0o077:
            raise EnvFileError(f"{path} is mode {stat.S_IMODE(info.st_mode):03o}; it must be 600")
        try:
            return handle.read()
        except UnicodeDecodeError:
            raise EnvFileError(f"{path} is not UTF-8 text") from None


def parse_env_text(text: str, need: Sequence[str]) -> dict[str, str]:
    """Values for the needed names. Other names in the shared file are ignored."""
    found: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.fullmatch(line)
        if not match:
            raise EnvFileError(f"line {number} is not NAME=value")
        name, value = match.group(1), match.group(2).strip()
        if name not in need:
            continue
        if name in found:
            raise EnvFileError(f"line {number} repeats {name}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        found[name] = value
    missing = [name for name in need if not found.get(name)]
    if missing:
        raise EnvFileError("missing a value for " + ", ".join(missing))
    return found


def child_environment(base: Mapping[str, str], values: Mapping[str, str]) -> dict[str, str]:
    """Inherited environment, minus anything that could redirect the tools, plus the values."""
    result = {k: v for k, v in base.items() if k not in DROPPED}
    for name, value in values.items():
        result[PROCESS_NAME[name]] = value
    result["EXPANSO_CLI_CONFIG_DIR"] = str(CLI_CONFIG_DIR)
    return result


def needed_names(with_bootstrap: bool) -> list[str]:
    return [ENDPOINT, API_KEY, *([BOOTSTRAP] if with_bootstrap else [])]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--env-file", type=Path, help="credentials file (default: RI_ENV_FILE, then ./.env)"
    )
    parser.add_argument(
        "--with-bootstrap",
        action="store_true",
        help="also pass EXPANSO_EDGE_BOOTSTRAP_TOKEN (just workspace-init only)",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("cloud-env: pass a command after --", file=sys.stderr)
        return 2
    try:
        path = args.env_file or default_env_file()
        values = parse_env_text(read_env_file(path), needed_names(args.with_bootstrap))
    except EnvFileError as error:
        print(f"cloud-env: {error}", file=sys.stderr)
        return 2
    try:
        os.execvpe(command[0], command, child_environment(os.environ, values))
    except OSError as error:
        print(f"cloud-env: cannot run {command[0]} ({error.strerror})", file=sys.stderr)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
