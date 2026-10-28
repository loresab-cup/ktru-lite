"""The API tests of the pipeline run without accounts; tests/test_auth.py turns them on."""

import os

os.environ.setdefault("AUTH_REQUIRED", "0")
