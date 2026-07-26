#!/usr/bin/env sh
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$REPO_DIR/scripts/install.py" "$@"
