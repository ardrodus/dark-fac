"""Tests for crucible/test_runner.py — failure classification and command building."""
from __future__ import annotations

import pytest

from dark_factory.crucible.test_runner import (
    FailureClass,
    TestMode,
    _build_run_command,
    classify_failure,
)


class TestClassifyFailure:
    def test_assertion_error_is_real_bug(self) -> None:
        cls = classify_failure("test_login", "AssertionError: expected 200 got 500")
        assert cls == FailureClass.REAL_BUG

    def test_type_error_is_real_bug(self) -> None:
        cls = classify_failure("test_api", "TypeError: cannot read property 'id' of undefined")
        assert cls == FailureClass.REAL_BUG

    def test_timeout_is_flaky(self) -> None:
        cls = classify_failure("test_slow", "TimeoutError: waiting for selector timed out")
        assert cls == FailureClass.FLAKY

    def test_element_not_found_is_flaky(self) -> None:
        cls = classify_failure("test_ui", "Error: element not found within timeout")
        assert cls == FailureClass.FLAKY

    def test_connection_refused_is_env(self) -> None:
        cls = classify_failure("test_db", "ECONNREFUSED 127.0.0.1:5432")
        assert cls == FailureClass.ENV_ISSUE

    def test_dns_failure_is_env(self) -> None:
        cls = classify_failure("test_api", "DNS resolution failed for db-host")
        assert cls == FailureClass.ENV_ISSUE

    def test_requires_live_is_needs_live(self) -> None:
        cls = classify_failure("test_ext", "skip: requires live external service")
        assert cls == FailureClass.NEEDS_LIVE

    def test_aws_url_is_needs_live(self) -> None:
        cls = classify_failure("test_s3", "Failed to connect to bucket.s3.amazonaws.com")
        assert cls == FailureClass.NEEDS_LIVE

    def test_priority_needs_live_over_env(self) -> None:
        # NEEDS_LIVE should win over ENV_ISSUE
        cls = classify_failure("test_x", "ECONNREFUSED to api.amazonaws.com")
        assert cls == FailureClass.NEEDS_LIVE

    def test_unknown_defaults_to_real_bug(self) -> None:
        cls = classify_failure("test_x", "something unexpected happened")
        assert cls == FailureClass.REAL_BUG

    def test_http_4xx_is_real_bug(self) -> None:
        cls = classify_failure("test_api", "Response: HTTP 404 Not Found")
        assert cls == FailureClass.REAL_BUG

    def test_logs_context_used(self) -> None:
        cls = classify_failure("test_x", "test failed", logs="ECONNREFUSED")
        assert cls == FailureClass.ENV_ISSUE


class TestBuildRunCommand:
    def test_playwright_default(self) -> None:
        cmd = _build_run_command("playwright")
        assert "npx playwright test" in cmd
        assert "--reporter=json" in cmd

    def test_pytest_default(self) -> None:
        cmd = _build_run_command("pytest")
        assert "pytest" in cmd
        assert "--json-report" in cmd

    def test_jest_default(self) -> None:
        cmd = _build_run_command("jest")
        assert "npx jest" in cmd
        assert "--json" in cmd

    def test_custom_reporter(self) -> None:
        cmd = _build_run_command("playwright", reporter_json="--custom-reporter")
        assert "--custom-reporter" in cmd

    def test_specific_files(self) -> None:
        cmd = _build_run_command("playwright", test_files=["tests/a.spec.ts", "tests/b.spec.ts"])
        assert "tests/a.spec.ts" in cmd
        assert "tests/b.spec.ts" in cmd

    def test_unknown_framework_defaults(self) -> None:
        cmd = _build_run_command("unknown-fw")
        assert "npx playwright test" in cmd


class TestModeEnum:
    def test_enum_values(self) -> None:
        assert TestMode.SMOKE.value == "smoke"
        assert TestMode.FULL.value == "full"
        assert TestMode.REGRESSION.value == "regression"
