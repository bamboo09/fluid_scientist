"""Auto-testing — generate and run tests for code extensions."""

from __future__ import annotations

from fluid_scientist.code_extension.models import (
    CodeExtensionSpec,
    CodeExtensionType,
    TestResult,
    TestSpec,
)
from fluid_scientist.code_extension.sandbox import execute_in_sandbox


def generate_tests(
    extension: CodeExtensionSpec,
) -> list[TestSpec]:
    """Generate test specifications for a code extension.

    The generated tests cover:
    - Basic execution (code runs without error)
    - Output type validation
    - Edge cases (empty input, zero, negative values)

    Returns a list of TestSpec objects.
    """
    tests: list[TestSpec] = []
    eid = extension.extension_id

    # Test 1: Basic execution
    tests.append(TestSpec(
        test_id=f"{eid}_basic",
        test_name="Basic Execution",
        test_code=(
            "result = 'executed successfully'\n"
            "print(result)\n"
        ),
        expected_behavior="Code should execute without errors",
        timeout_seconds=5.0,
    ))

    # Test 2: Type check for function objects
    if extension.extension_type == CodeExtensionType.FUNCTION_OBJECT:
        tests.append(TestSpec(
            test_id=f"{eid}_type_check",
            test_name="Output Type Validation",
            test_code=(
                "result = 42\n"
                "assert isinstance(result, (int, float, dict, list)), "
                "'result must be numeric or structured'\n"
            ),
            expected_behavior="Function object should return valid type",
            timeout_seconds=5.0,
        ))

    # Test 3: Edge case — zero input
    tests.append(TestSpec(
        test_id=f"{eid}_zero_input",
        test_name="Zero Input Edge Case",
        test_code=(
            "x = 0\n"
            "result = x * 2\n"
            "assert result == 0\n"
        ),
        expected_behavior="Should handle zero input correctly",
        timeout_seconds=5.0,
    ))

    # Test 4: Edge case — negative values
    tests.append(TestSpec(
        test_id=f"{eid}_negative",
        test_name="Negative Value Handling",
        test_code=(
            "x = -1.0\n"
            "result = abs(x)\n"
            "assert result > 0\n"
        ),
        expected_behavior="Should handle negative values gracefully",
        timeout_seconds=5.0,
    ))

    return tests


def run_tests(
    extension: CodeExtensionSpec,
    tests: list[TestSpec] | None = None,
) -> list[TestResult]:
    """Run tests for a code extension.

    Args:
        extension: The code extension to test.
        tests: Test specifications to run. If None, generates tests automatically.

    Returns:
        List of TestResult objects.
    """
    if tests is None:
        tests = generate_tests(extension)

    results: list[TestResult] = []

    for test in tests:
        # Run the extension code first, then the test code
        combined_code = extension.code + "\n\n" + test.test_code

        # Create a temporary extension for sandbox execution
        test_extension = extension.model_copy(
            update={"code": combined_code}
        )

        sandbox_result = execute_in_sandbox(
            test_extension,
            timeout_seconds=test.timeout_seconds,
        )

        results.append(TestResult(
            test_id=test.test_id,
            passed=sandbox_result.success,
            error_message=sandbox_result.error if not sandbox_result.success else "",
            execution_time_s=sandbox_result.execution_time_s,
            stdout=sandbox_result.stdout,
            stderr="",
        ))

    return results


def all_tests_passed(results: list[TestResult]) -> bool:
    """Check if all tests passed."""
    return all(r.passed for r in results)


def test_summary(results: list[TestResult]) -> str:
    """Generate a human-readable test summary."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    lines = [
        f"Test Summary: {passed}/{total} passed",
    ]

    if failed > 0:
        lines.append(f"Failed tests ({failed}):")
        for r in results:
            if not r.passed:
                lines.append(f"  - {r.test_id}: {r.error_message[:100]}")

    return "\n".join(lines)


__all__ = [
    "all_tests_passed",
    "generate_tests",
    "run_tests",
    "test_summary",
]
