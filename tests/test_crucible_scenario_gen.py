"""Tests for crucible/scenario_gen.py — scenario test generation from PR diffs."""
from __future__ import annotations

from pathlib import Path

import pytest

from dark_factory.crucible.framework_detect import FrameworkProfile
from dark_factory.crucible.scenario_gen import (
    ScenarioGenResult,
    ScenarioTest,
    _extract_changed_files,
    _generate_fallback,
    _parse_agent_response,
    _summarize_diff,
    generate_scenarios,
    write_scenarios,
)

_PLAYWRIGHT = FrameworkProfile(
    name="playwright", language="TypeScript",
    install_cmd="npm install @playwright/test",
    run_cmd="npx playwright test",
    config_file="playwright.config.ts",
    reporter_json="--reporter=json",
)

_HTTPX = FrameworkProfile(
    name="httpx", language="Python",
    install_cmd="pip install httpx pytest pytest-json-report",
    run_cmd="pytest tests/api/",
    config_file="pytest.ini",
    reporter_json="--json-report",
)

_SAMPLE_DIFF = """\
diff --git a/src/checkout.ts b/src/checkout.ts
index abc1234..def5678 100644
--- a/src/checkout.ts
+++ b/src/checkout.ts
@@ -10,6 +10,12 @@ export function checkout(cart: Cart) {
+  if (cart.discountCode) {
+    total = applyDiscount(total, cart.discountCode);
+  }
diff --git a/src/api/routes.ts b/src/api/routes.ts
index 111..222 100644
--- a/src/api/routes.ts
+++ b/src/api/routes.ts
@@ -5,3 +5,8 @@
+router.post('/discount', validateDiscount);
"""


class TestSummarizeDiff:
    def test_counts_files(self) -> None:
        summary = _summarize_diff(_SAMPLE_DIFF)
        assert "2" in summary  # 2 files changed
        assert "checkout.ts" in summary

    def test_counts_additions(self) -> None:
        summary = _summarize_diff(_SAMPLE_DIFF)
        assert "+4" in summary or "+5" in summary  # additions


class TestExtractChangedFiles:
    def test_extracts_paths(self) -> None:
        files = _extract_changed_files(_SAMPLE_DIFF)
        assert "src/checkout.ts" in files
        assert "src/api/routes.ts" in files

    def test_empty_diff(self) -> None:
        assert _extract_changed_files("") == []


class TestParseAgentResponse:
    def test_parses_single_test(self) -> None:
        response = '''Some preamble text.

<<<SCENARIO_TEST file="tests/pr-42-checkout.spec.ts">>>
import { test, expect } from '@playwright/test';

test('checkout applies discount', async ({ page }) => {
  await page.goto('/checkout');
  await expect(page.locator('.total')).toBeVisible();
});
<<<END_SCENARIO_TEST>>>
'''
        tests = _parse_agent_response(response, 42)
        assert len(tests) == 1
        # stem of "pr-42-checkout.spec.ts" is "pr-42-checkout.spec"
        # after removing "pr-42-" prefix -> "checkout.spec"
        assert tests[0].name == "checkout.spec"
        assert tests[0].framework == "playwright"
        assert "checkout applies discount" in tests[0].test_code

    def test_parses_multiple_tests(self) -> None:
        response = '''
<<<SCENARIO_TEST file="tests/pr-10-auth.spec.ts">>>
test code 1
<<<END_SCENARIO_TEST>>>

<<<SCENARIO_TEST file="tests/pr-10-api.spec.ts">>>
test code 2
<<<END_SCENARIO_TEST>>>
'''
        tests = _parse_agent_response(response, 10)
        assert len(tests) == 2

    def test_python_framework_detected(self) -> None:
        response = '''
<<<SCENARIO_TEST file="tests/pr-5-smoke.py">>>
def test_smoke():
    assert True
<<<END_SCENARIO_TEST>>>
'''
        tests = _parse_agent_response(response, 5)
        assert tests[0].framework == "pytest"

    def test_caps_at_max_files(self) -> None:
        parts = []
        for i in range(10):
            parts.append(f'<<<SCENARIO_TEST file="tests/pr-1-t{i}.spec.ts">>>\ncode\n<<<END_SCENARIO_TEST>>>')
        response = "\n".join(parts)
        tests = _parse_agent_response(response, 1)
        assert len(tests) == 5  # _MAX_TEST_FILES


class TestGenerateFallback:
    def test_playwright_fallback(self) -> None:
        response = _generate_fallback(42, _PLAYWRIGHT, ["src/app.ts"])
        assert "pr-42" in response
        assert "SCENARIO_TEST" in response
        assert "playwright" in response.lower() or "expect" in response

    def test_httpx_fallback(self) -> None:
        response = _generate_fallback(7, _HTTPX, ["main.py"])
        assert "pr-7" in response
        assert ".py" in response
        assert "def test_" in response
        assert "httpx" in response


class TestGenerateScenarios:
    def test_empty_diff_returns_error(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        cruc = tmp_path / "cruc"
        cruc.mkdir()
        result = generate_scenarios(ws, cruc, 1, "", (_PLAYWRIGHT,))
        assert result.error
        assert len(result.tests) == 0

    def test_fallback_generates_test(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        cruc = tmp_path / "cruc"
        cruc.mkdir()
        result = generate_scenarios(ws, cruc, 42, _SAMPLE_DIFF, (_PLAYWRIGHT,))
        assert len(result.tests) >= 1
        assert result.pr_number == 42

    def test_agent_fn_used(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        cruc = tmp_path / "cruc"
        cruc.mkdir()

        def mock_agent(prompt: str) -> str:
            return '''<<<SCENARIO_TEST file="tests/pr-99-mock.spec.ts">>>
test('mock test', async () => { expect(true).toBe(true); });
<<<END_SCENARIO_TEST>>>'''

        result = generate_scenarios(
            ws, cruc, 99, _SAMPLE_DIFF, (_PLAYWRIGHT,), agent_fn=mock_agent,
        )
        assert len(result.tests) == 1
        assert result.tests[0].name == "mock.spec"


class TestWriteScenarios:
    def test_writes_files(self, tmp_path: Path) -> None:
        cruc = tmp_path / "crucible"
        cruc.mkdir()
        result = ScenarioGenResult(
            tests=(
                ScenarioTest(
                    name="checkout", file_path="tests/pr-42-checkout.spec.ts",
                    test_code="test('checkout', () => {});", framework="playwright",
                    category="smoke",
                ),
            ),
            pr_number=42,
            pr_diff_summary="1 file changed",
            frameworks_used=("playwright",),
        )
        written = write_scenarios(cruc, result)
        assert len(written) == 1
        assert (cruc / "tests" / "pr-42-checkout.spec.ts").is_file()
