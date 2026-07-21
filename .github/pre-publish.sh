#!/usr/bin/env bash
# Pre-publish gate for the reusable publish workflow: pyright + unit tests over
# skills/_scripts, mirroring the prior publish gate so a direct push to main or a
# workflow_dispatch publish can't ship a red suite.
set -euo pipefail
python3 -m pip install -r requirements-dev.txt
pyright skills/_scripts/
python -m unittest discover -s skills/_scripts/tests -p 'test_*.py'
