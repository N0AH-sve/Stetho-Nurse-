"""Identifier formats shared by several parts of STETHO."""

import re

# Profile and board names become folder names: no separator, no leading dot or dash.
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
SENSOR_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
# The same patterns as `ActionCode` and the prescription target in the API.
ACTION_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
ACTION_TARGET = re.compile(r"[A-Za-z0-9_]{1,64}")
