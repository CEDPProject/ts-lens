"""Test isolation: never pick up this machine's local accounts or external-LLM key.

`unittest discover -s tests` imports this package first, so the defaults below
apply before `app` is imported. Individual tests that need a file point the
same variables at a temp path.
"""
import os

os.environ.setdefault("TS_PATTERN_USERS_FILE", "/nonexistent/ts-lens-tests/users.yaml")
os.environ.setdefault("TS_PATTERN_EXTERNAL_LLM_FILE", "/nonexistent/ts-lens-tests/external_llm.json")
