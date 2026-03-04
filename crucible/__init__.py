"""Crucible test validation — framework detection, scenario generation, graduation."""

from dark_factory.crucible.framework_detect import (
    DetectionResult,
    FrameworkProfile,
    build_detection_result,
    detect_frameworks,
    ensure_frameworks,
)
from dark_factory.crucible.graduation import GraduationResult, graduate_tests
from dark_factory.crucible.orchestrator import CrucibleResult, CrucibleVerdict
from dark_factory.crucible.repo_provision import (
    CrucibleRepoResult,
    manage_crucible_repo,
    provision_crucible_repo,
)
from dark_factory.crucible.scenario_gen import (
    ScenarioGenResult,
    ScenarioTest,
    generate_scenarios,
    write_scenarios,
)
from dark_factory.crucible.test_runner import (
    ClassifiedFailure,
    FailureClass,
    RunResult,
    TestMode,
    classify_failure,
    run_tests,
)

__all__ = [
    "ClassifiedFailure",
    "CrucibleResult",
    "CrucibleRepoResult",
    "CrucibleVerdict",
    "DetectionResult",
    "FailureClass",
    "FrameworkProfile",
    "GraduationResult",
    "RunResult",
    "ScenarioGenResult",
    "ScenarioTest",
    "TestMode",
    "build_detection_result",
    "classify_failure",
    "detect_frameworks",
    "ensure_frameworks",
    "generate_scenarios",
    "graduate_tests",
    "manage_crucible_repo",
    "provision_crucible_repo",
    "run_tests",
    "write_scenarios",
]
