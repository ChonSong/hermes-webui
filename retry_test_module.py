"""
Test module for Riptide retry mechanism.

This module intentionally introduces a test failure scenario
to validate Riptide's CI retry system.
"""

def flaky_function():
    """This function returns the wrong value to trigger test failures."""
    return None  # Bug: should return {}


def another_function():
    """This function works correctly."""
    return {"status": "ok"}
# trigger workflow
