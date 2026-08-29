"""The shipped doubles and queries, exercised before the suite depends on them."""

import discord

from squid_ui.planning.adapter import AdapterCapability
from squid_ui.planning.limits import LIMITS
from squid_ui.primitives import Heading, Panel, RoutedButton, Row, Sep, Text
from squid_ui_discord import testing as sd


class TestPayloadQueries:
    def test_texts_read_the_payload_and_agree_with_walking_the_objects(self) -> None:
        """The payload is what Discord sees; the object tree is how discord.py got there.

        Pinning the agreement is what makes replacing the object walkers a safe substitution
        rather than a behaviour change.
        """
        view = sd.static_view([Heading("Title"), Text("one"), Panel(children=(Text("inner"), Sep())), Text("two")])

        walked = [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]

        assert sd.payload_texts(view) == walked
        assert sd.payload_texts(view) == ["## Title", "one", "inner", "two"]

    def test_labels_and_custom_ids_come_back_in_render_order(self) -> None:
        view = sd.static_view([Row((RoutedButton("First", "a"), RoutedButton("Second", "b")))])

        assert sd.payload_labels(view) == ["First", "Second"]
        assert sd.payload_custom_ids(view) == ["a", "b"]

    def test_a_text_query_does_not_match_a_string_that_is_only_a_custom_id(self) -> None:
        """The failure mode of `str(view.to_components())`: it cannot tell the two apart."""
        view = sd.static_view([Row((RoutedButton("Go", "secret-id"),))])

        assert "secret-id" in str(view.to_components())
        assert not any("secret-id" in text for text in sd.payload_texts(view))

    def test_the_queries_also_accept_a_raw_component_payload(self) -> None:
        view = sd.static_view([Text("body")])

        assert sd.payload_texts(view.to_components()) == ["body"]


class TestConstruction:
    async def test_interaction_harness_records_responses_and_exposes_its_source(self) -> None:
        harness = sd.interaction_harness(user_id=7)

        await harness.source.response.send_message("hello", ephemeral=True)
        await harness.source.followup.send("again")

        assert harness.source.user.id == 7
        assert [record.args for record in harness.sends] == [("hello",), ("again",)]
        assert harness.sends[0].kwargs["ephemeral"] is True

    async def test_message_harness_records_edits_and_injects_faults_explicitly(self) -> None:
        failure = sd.http_error(503)
        harness = sd.message_harness().fail_edits_with(failure)

        try:
            await harness.source.edit(content="new")
        except discord.HTTPException as error:
            assert error is failure
        else:
            raise AssertionError("the configured edit fault was not raised")

        assert harness.edits == [sd.CallRecord((), {"content": "new"})]

    def test_a_target_profile_supplies_exactly_the_capabilities_it_is_given(self) -> None:
        target = sd.target_profile("narrow", capabilities=frozenset({AdapterCapability.MODAL_FORMS}))

        assert target.adapter.capabilities == frozenset({AdapterCapability.MODAL_FORMS})
        assert target.limits == LIMITS

    def test_a_layout_view_holds_the_items_in_the_order_given(self) -> None:
        view = sd.layout_view(sd.action_row(discord.ui.Button(label="Go", custom_id="go")))

        assert sd.payload_labels(view) == ["Go"]
        assert view.timeout is None


class TestFailureInjection:
    def test_a_generic_failure_reports_its_status(self) -> None:
        assert sd.http_error().status == 500
        assert sd.http_error(503).status == 503

    def test_the_stale_failure_carries_the_code_the_recovery_path_keys_on(self) -> None:
        """Recovery keys on code 10015, not on the 404 -- an unknown webhook is what an
        expired interaction token looks like from Discord's side."""
        stale = sd.stale_http_error()

        assert (stale.status, stale.code) == (404, 10015)
