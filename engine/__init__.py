"""Factory Pipeline Engine.

DOT-based pipeline runner for orchestrating multi-stage AI workflows.
"""

from dark_factory.engine.conditions import evaluate_condition
from dark_factory.engine.config import EngineConfig, load_engine_config
from dark_factory.engine.events import (
    CheckpointSaved,
    EventEmitter,
    InterviewCompleted,
    InterviewStarted,
    InterviewTimeout,
    ParallelBranchCompleted,
    ParallelBranchStarted,
    ParallelCompleted,
    ParallelStarted,
    PipelineCompleted,
    PipelineEvent,
    PipelineFailed,
    PipelineStarted,
    StageCompleted,
    StageFailed,
    StageRetrying,
    StageStarted,
)
from dark_factory.engine.graph import Edge, Graph, Node, NodeShape
from dark_factory.engine.handlers import (
    Answer,
    AutoApproveInterviewer,
    CallbackInterviewer,
    CodergenBackend,
    CodergenHandler,
    ConditionalHandler,
    ExitHandler,
    HumanHandler,
    Interviewer,
    Question,
    QuestionType,
    QueueInterviewer,
    StartHandler,
    ToolHandler,
    ask_question_via_ask,
    register_default_handlers,
)
from dark_factory.engine.parser import parse_dot
from dark_factory.engine.runner import (
    RETRY_PRESETS,
    Checkpoint,
    Handler,
    HandlerRegistry,
    HandlerResult,
    Outcome,
    PipelineContext,
    PipelineResult,
    PipelineStatus,
    get_retry_preset,
    run_pipeline,
    select_edge,
)
from dark_factory.engine.sdk import ExecuteConfig, execute
from dark_factory.engine.stylesheet import (
    Stylesheet,
    apply_stylesheet,
    parse_stylesheet,
)
from dark_factory.engine.transforms import (
    GraphTransform,
    VariableExpansionTransform,
    apply_transforms,
)

__all__ = [
    # Parser
    "parse_dot",
    # Stylesheet
    "Stylesheet",
    "parse_stylesheet",
    "apply_stylesheet",
    # Graph model
    "Graph",
    "Node",
    "Edge",
    "NodeShape",
    # Engine
    "run_pipeline",
    "select_edge",
    "PipelineResult",
    "PipelineStatus",
    "PipelineContext",
    "HandlerResult",
    "Outcome",
    "Handler",
    "HandlerRegistry",
    "Checkpoint",
    "RETRY_PRESETS",
    "get_retry_preset",
    # Handlers
    "StartHandler",
    "ExitHandler",
    "ConditionalHandler",
    "ToolHandler",
    "CodergenHandler",
    "CodergenBackend",
    "HumanHandler",
    "Interviewer",
    "AutoApproveInterviewer",
    "CallbackInterviewer",
    "QueueInterviewer",
    "Question",
    "Answer",
    "QuestionType",
    "ask_question_via_ask",
    "register_default_handlers",
    # Transforms (Spec §9, §11.11)
    "GraphTransform",
    "VariableExpansionTransform",
    "apply_transforms",
    # Conditions
    "evaluate_condition",
    # Events (Spec §9.6)
    "PipelineEvent",
    "EventEmitter",
    "PipelineStarted",
    "PipelineCompleted",
    "PipelineFailed",
    "StageStarted",
    "StageCompleted",
    "StageFailed",
    "StageRetrying",
    "ParallelStarted",
    "ParallelBranchStarted",
    "ParallelBranchCompleted",
    "ParallelCompleted",
    "InterviewStarted",
    "InterviewCompleted",
    "InterviewTimeout",
    "CheckpointSaved",
    # SDK (high-level API)
    "execute",
    "ExecuteConfig",
    # Engine config (US-204)
    "EngineConfig",
    "load_engine_config",
]
