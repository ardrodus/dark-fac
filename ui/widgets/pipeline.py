"""Compatibility re-export — canonical module is pipeline_flow.py."""

from dark_factory.ui.widgets.pipeline_flow import (
    PipelineConnector,
    PipelineConnectorProtocol,
    PipelineFlowDiagram,
    PipelineFlowDiagramProtocol,
    PipelineNode,
    PipelineNodeProtocol,
)

__all__ = [
    "PipelineConnector",
    "PipelineConnectorProtocol",
    "PipelineFlowDiagram",
    "PipelineFlowDiagramProtocol",
    "PipelineNode",
    "PipelineNodeProtocol",
]
