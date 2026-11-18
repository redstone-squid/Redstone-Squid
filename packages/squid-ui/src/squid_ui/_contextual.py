"""Execution guard shared by authoring and contextual evaluation."""

from contextvars import ContextVar

EVALUATING: ContextVar[bool] = ContextVar("squid_ui_context_evaluating", default=False)
