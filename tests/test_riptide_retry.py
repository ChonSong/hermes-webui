"""
Intentional test failure for validating Riptide retry mechanism.

This test file is deliberately broken to test whether Riptide detects
CI failures and retries automatically.
"""

import pytest


def test_this_should_fail_deliberately():
    """
    This test is intentionally broken.
    It asserts something false to trigger a CI failure.
    """
    result = None  # Bug: should be a dict
    assert result == {"status": "ok"}, f"Expected dict, got {result}"


def test_this_passes():
    """A passing test for comparison."""
    assert 1 + 1 == 2
