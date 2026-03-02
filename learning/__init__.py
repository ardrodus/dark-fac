"""Learning system — agents for project discovery and domain understanding."""
from factory.learning.api_explorer import APIExplorerResult, Endpoint, run_api_explorer
from factory.learning.data_mapper import DataMapperResult, run_data_mapper
from factory.learning.domain_expert import DomainExpertResult, run_domain_expert
from factory.learning.feedback_aggregation import (
    FeedbackInstance,
    apply_widespread_fix,
    extract_feedback,
    generate_digest,
    is_widespread,
)
from factory.learning.integration_analyst import IntegrationResult, run_integration_analyst
from factory.learning.orchestrator import LearningResult, run_full_learning, run_incremental_learning
from factory.learning.scout import ScoutResult, run_scout
from factory.learning.test_archaeologist import TestArchResult, run_test_archaeologist

__all__ = [
    "APIExplorerResult",
    "DataMapperResult",
    "DomainExpertResult",
    "Endpoint",
    "FeedbackInstance",
    "IntegrationResult",
    "LearningResult",
    "ScoutResult",
    "TestArchResult",
    "apply_widespread_fix",
    "extract_feedback",
    "generate_digest",
    "is_widespread",
    "run_api_explorer",
    "run_data_mapper",
    "run_domain_expert",
    "run_full_learning",
    "run_incremental_learning",
    "run_integration_analyst",
    "run_scout",
    "run_test_archaeologist",
]
