"""
Sanity tests for agent/validation.py that don't need a live Exasol
instance — run with:

    python -m tests.test_validation
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.validation import validate_udf, TestCase


def test_correct_udf_passes():
    code = """
def z_score(value, mean, stddev):
    return (value - mean) / stddev
"""
    cases = [
        TestCase(args=[10, 5, 2], expected=2.5, description="basic z-score"),
        TestCase(args=[5, 5, 2], expected=0.0, description="value equals mean"),
    ]
    report = validate_udf(code, "z_score", cases)
    assert report.passed, report.as_text()
    print("test_correct_udf_passes: OK")


def test_buggy_udf_fails():
    code = """
def z_score(value, mean, stddev):
    return (value - mean) * stddev  # bug: should divide
"""
    cases = [TestCase(args=[10, 5, 2], expected=2.5, description="basic z-score")]
    report = validate_udf(code, "z_score", cases)
    assert not report.passed
    print("test_buggy_udf_fails: OK")


def test_crashing_udf_fails_gracefully():
    code = """
def divide(a, b):
    return a / b
"""
    cases = [TestCase(args=[10, 0], expected=999, description="division by zero")]
    report = validate_udf(code, "divide", cases)
    assert not report.passed
    print("test_crashing_udf_fails_gracefully: OK")


if __name__ == "__main__":
    test_correct_udf_passes()
    test_buggy_udf_fails()
    test_crashing_udf_fails_gracefully()
    print("All validation harness sanity tests passed.")
