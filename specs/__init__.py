"""Specification generation — PRD, design docs, and related artifacts."""

from dark_factory.specs.api_contract_generator import (
    ContractResult,
    ContractType,
    generate_api_contract,
)
from dark_factory.specs.interface_generator import (
    InterfaceLang,
    InterfaceResult,
    generate_interfaces,
)
from dark_factory.specs.schema_generator import (
    SchemaResult,
    SchemaType,
    generate_schema,
)
from dark_factory.specs.test_strategy_generator import (
    TestStrategyResult,
    generate_test_strategy,
)

__all__ = [
    "ContractResult", "ContractType", "generate_api_contract",
    "InterfaceLang", "InterfaceResult", "generate_interfaces",
    "SchemaResult", "SchemaType", "generate_schema",
    "TestStrategyResult", "generate_test_strategy",
]
