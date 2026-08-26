"""Package-local test configuration; the repo-root conftest does not reach this directory."""

import os

from hypothesis import settings

settings.register_profile(
    "ci",
    max_examples=500,
    deadline=None,
    print_blob=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))
