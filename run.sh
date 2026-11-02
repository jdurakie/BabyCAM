#!/bin/sh
set -eu

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 1
fi

exec uv run --locked babycam "$@"
