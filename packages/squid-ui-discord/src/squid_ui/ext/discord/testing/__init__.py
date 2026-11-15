"""Gateway-free Discord doubles and assertions for serialized component payloads.

Use ``stage`` to present a component through an isolated running UI runtime and drive its
controls. Lower-level tests can send a MessageRoot to ``delivered_to(message_harness())``
and call dispatch with an ``interaction_harness``. The harnesses record sends and edits.

The ``payload_*`` queries inspect serialized controls, while ``assert_within_limits``
checks supported size limits. These helpers do not implement Discord's complete validation
or network behavior. Import them from ``squid_ui.ext.discord.testing``; the package root does
not re-export testing helpers.
"""

from squid_ui.ext.discord.testing.commands import invoke, invoke_context_menu
from squid_ui.ext.discord.testing.fixtures import (
    action_row,
    commit_classic_render,
    commit_render,
    drain,
    layout_view,
    static_view,
    target_profile,
    without_capabilities,
)
from squid_ui.ext.discord.testing.harness import (
    AsyncCallRecorder,
    CallRecord,
    ContextHarness,
    InteractionHarness,
    MessageHarness,
    delivered_to,
    http_error,
    interaction_harness,
    message_harness,
    stale_http_error,
)
from squid_ui.ext.discord.testing.payloads import (
    BuiltView,
    ComponentPayload,
    assert_within_limits,
    iter_component_payloads,
    modal_problems,
    payload_custom_ids,
    payload_labels,
    payload_problems,
    payload_texts,
)
from squid_ui.ext.discord.testing.queries import index_tree, query
from squid_ui.ext.discord.testing.staging import StagedControl, StagedForm, StagedUI, stage

__all__ = [
    "AsyncCallRecorder",
    "BuiltView",
    "CallRecord",
    "ComponentPayload",
    "ContextHarness",
    "InteractionHarness",
    "MessageHarness",
    "StagedControl",
    "StagedForm",
    "StagedUI",
    "action_row",
    "assert_within_limits",
    "commit_classic_render",
    "commit_render",
    "delivered_to",
    "drain",
    "http_error",
    "index_tree",
    "interaction_harness",
    "invoke",
    "invoke_context_menu",
    "iter_component_payloads",
    "layout_view",
    "message_harness",
    "modal_problems",
    "payload_custom_ids",
    "payload_labels",
    "payload_problems",
    "payload_texts",
    "query",
    "stage",
    "stale_http_error",
    "static_view",
    "target_profile",
    "without_capabilities",
]
