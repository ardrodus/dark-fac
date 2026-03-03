"""Crucible test orchestrator — build, run, and evaluate end-to-end tests."""

from dark_factory.crucible.coordinator import (
    CrucibleCoordinatorConfig,
    TwoRoundResult,
    run_crucible_pipeline,
    to_crucible_result,
)
from dark_factory.crucible.framework_detect import (
    DetectionResult,
    FrameworkProfile,
    build_detection_result,
    detect_frameworks,
    ensure_frameworks,
)
from dark_factory.crucible.graduation import GraduationResult, graduate_tests
from dark_factory.crucible.orchestrator import CrucibleVerdict, run_crucible, run_sharded_crucible
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
from dark_factory.crucible.sharding import ShardResult, merge_verdicts, partition_tests
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
    "CrucibleCoordinatorConfig",
    "CrucibleRepoResult",
    "CrucibleVerdict",
    "DetectionResult",
    "build_detection_result",
    "FailureClass",
    "FrameworkProfile",
    "GraduationResult",
    "RunResult",
    "ScenarioGenResult",
    "ScenarioTest",
    "ShardResult",
    "TestMode",
    "TwoRoundResult",
    "classify_failure",
    "detect_frameworks",
    "ensure_frameworks",
    "generate_scenarios",
    "graduate_tests",
    "manage_crucible_repo",
    "merge_verdicts",
    "partition_tests",
    "provision_crucible_repo",
    "run_crucible",
    "run_crucible_pipeline",
    "run_sharded_crucible",
    "run_tests",
    "to_crucible_result",
    "write_scenarios",
]
