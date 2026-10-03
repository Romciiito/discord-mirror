#!/bin/sh
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || exit 1
fi
if ! cmp -s requirements.txt .venv/requirements.txt; then
  .venv/bin/python -m pip install -r requirements.txt || exit 1
  cp requirements.txt .venv/requirements.txt
fi
exec .venv/bin/python -m mirror
