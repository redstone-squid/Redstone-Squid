"""The shipped test helpers, exercised before anything else depends on them."""

import subprocess

import pytest

import squid_ui as sl
from squid_ui import testing as st
from squid_ui.interactions import Visibility
from squid_ui.semantic import ActionControl, ActionControls, Choices, Paragraph


async def _pressed(_event: sl.PressEvent) -> None: ...


def _controls(*keys: str) -> ActionControls:
    return ActionControls(
        tuple(ActionControl(key=key, label=key.title(), on_trigger=_pressed) for key in keys),
        key="bar",
    )


class TestWalk:
    def test_it_reaches_controls_the_layout_child_fields_do_not_name(self) -> None:
        """`ActionControls.items` is deliberately not a layout child, and every hand-written
        walker this replaces had to remember to special-case it."""
        tree = sl.stack(sl.paragraph("body"), _controls("save", "close"))

        assert st.keys(tree) == ["bar", "save", "close"]

    def test_it_descends_through_sequences_and_nested_containers(self) -> None:
        tree = [sl.stack(sl.stack(sl.paragraph("deep")))]

        assert st.texts(tree) == ["deep"]

    def test_it_visits_each_node_once_even_when_one_object_is_shared(self) -> None:
        shared = sl.paragraph("once")

        assert st.texts(sl.stack(shared, shared)) == ["once"]

    def test_a_bare_string_is_not_walked_as_a_sequence(self) -> None:
        assert list(st.walk("abc")) == []


class TestQueries:
    def test_texts_resolves_a_message_to_its_untranslated_template(self) -> None:
        tree = sl.paragraph(sl.text.Message("Hello {name}", {"name": "Ada"}))

        assert st.texts(tree) == ["Hello Ada"]

    def test_labels_reads_controls_and_texts_reads_content(self) -> None:
        tree = sl.stack(sl.paragraph("body"), _controls("save"))

        assert st.texts(tree) == ["body"]
        assert st.labels(tree) == ["Save"]

    def test_find_narrows_by_key_and_returns_the_node(self) -> None:
        tree = sl.stack(_controls("save", "close"))

        assert st.find(tree, ActionControl, key="close").label == "Close"

    def test_find_names_the_available_keys_when_there_is_no_match(self) -> None:
        with pytest.raises(AssertionError, match=r"no Choices keyed 'gone'.*'bar', 'save'"):
            st.find(sl.stack(_controls("save")), Choices, key="gone")

    def test_find_refuses_a_duplicate_rather_than_reading_past_it(self) -> None:
        """Two controls sharing a key is the defect; returning the first would hide it."""
        with pytest.raises(AssertionError, match="2 of ActionControl keyed 'save'"):
            st.find(sl.stack(_controls("save", "save")), ActionControl, key="save")

    def test_find_all_returns_render_order(self) -> None:
        tree = sl.stack(_controls("a", "b", "c"))

        assert [control.key for control in st.find_all(tree, ActionControl)] == ["a", "b", "c"]


class TestRenderTree:
    def test_it_expands_a_component_with_no_runtime_and_no_frontend(self) -> None:
        nodes = st.render_tree(st.text_component("hello", "world"))

        assert st.texts(nodes) == ["hello", "world"]
        assert all(isinstance(node, Paragraph) for node in nodes)

    def test_declared_state_is_reactive_and_a_callable_line_reads_it(self) -> None:
        subject = st.text_component(lambda self: f"count {self.count}", count=0)

        assert st.texts(st.render_tree(subject)) == ["count 0"]

        subject.count = 7  # type: ignore[attr-defined]

        assert st.texts(st.render_tree(subject)) == ["count 7"]


class TestRecordingResponder:
    async def test_it_records_what_the_handler_asked_the_frontend_for(self) -> None:
        responder = st.RecordingResponder()
        event = st.press_event(responder=responder)

        await event.acknowledge()
        await event.notice("saved", visibility=Visibility.PUBLIC)
        await event.redirect("https://example.invalid")
        event.invalidate()
        await event.finish()

        assert responder.acknowledged == 1
        assert responder.notices == [("saved", Visibility.PUBLIC)]
        assert responder.redirects == ["https://example.invalid"]
        assert responder.invalidations == 1
        assert responder.finished

    def test_two_responders_do_not_share_their_recordings(self) -> None:
        first = st.RecordingResponder()
        first.notices.append(("a", Visibility.PRIVATE))

        assert st.RecordingResponder().notices == []

    async def test_the_untouched_responder_names_the_call_that_reached_it(self) -> None:
        event = st.press_event(responder=st.UntouchedResponder())

        with pytest.raises(AssertionError, match="called notice"):
            await event.notice("nope")


class TestEvents:
    def test_each_event_carries_a_recording_responder_by_default(self) -> None:
        assert isinstance(st.press_event().responder, st.RecordingResponder)

    def test_a_choice_reports_what_settled_and_what_moved(self) -> None:
        event = st.choice_event("a", "b", added=["b"])

        assert (event.selected, event.added, event.removed) == (("a", "b"), ("b",), ())

    def test_a_selection_carries_its_values_and_a_submission_its_mapping(self) -> None:
        assert st.selection_event("a", "b").values == ("a", "b")
        assert st.submit_event({"name": "Ada"}).values == {"name": "Ada"}

    def test_a_submission_attempts_what_it_submits_unless_told_otherwise(self) -> None:
        assert st.submit_event({"name": "Ada"}).attempted == {"name": "Ada"}
        assert st.submit_event({}, attempted={"name": "!"}).attempted == {"name": "!"}


class TestManualClock:
    def test_the_monotonic_and_wall_readings_advance_together(self) -> None:
        clock = st.ManualClock()
        before = clock.utc()

        clock.advance(30)

        assert clock.monotonic() == 1_030.0
        assert clock() == 1_030.0
        assert (clock.utc() - before).total_seconds() == 30

    def test_it_does_not_move_on_its_own(self) -> None:
        clock = st.ManualClock()

        assert clock() == clock()


class TestImportIsolation:
    def test_the_engine_imports_with_no_transport_available(self) -> None:
        st.assert_imports_without("squid_ui", "discord", "anyio", "squid_storage")

    def test_it_fails_when_the_package_needs_what_was_blocked(self) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            st.assert_imports_without("squid_ui", "squid_reactivity")
