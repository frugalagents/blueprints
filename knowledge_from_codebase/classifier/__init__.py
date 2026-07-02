"""
classifier — LLM-powered business classification, rule extraction, and flow mapping.

This module provides three core components for analysing a codebase's
business semantics:

* **BusinessClassifier** – classifies every function/method into one of five
  categories (BUSINESS_RULE, BUSINESS_PROCESS, DATA_ACCESS,
  TECHNICAL_INFRASTRUCTURE, INTEGRATION) using an LLM.
* **RuleExtractor** – extracts BDD-style business rules from functions that
  have been classified as BUSINESS_RULE or BUSINESS_PROCESS.
* **FlowMapper** – identifies end-to-end business flows across domains and
  generates a plain-language codebase summary.

All LLM calls go through Amazon Bedrock's ``converse()`` API using
``anthropic.claude-sonnet-4-20250514`` by default.
"""

from .business_classifier import BusinessClassifier
from .rule_extractor import RuleExtractor
from .flow_mapper import FlowMapper

__all__ = [
    "BusinessClassifier",
    "RuleExtractor",
    "FlowMapper",
]
