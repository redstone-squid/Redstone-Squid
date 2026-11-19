import pytest

from squid.builds.domain import ShowcaseTag, read_showcase_tags
from squid.core.errors import JSONValue, ValidationError


def test_read_showcase_tags_keeps_raw_values_and_round_trips() -> None:
    answer = [{"key": "compact"}, {"key": "tick_speed", "value": "1.5"}, {"key": "seamless", "value": None}]

    tags = read_showcase_tags(answer)

    assert tags == (ShowcaseTag("compact"), ShowcaseTag("tick_speed", "1.5"), ShowcaseTag("seamless"))
    assert [tag.to_json() for tag in tags] == [
        {"key": "compact"},
        {"key": "tick_speed", "value": "1.5"},
        {"key": "seamless"},
    ]


@pytest.mark.parametrize(
    "answer",
    [
        "compact",
        ["compact"],
        [{"value": "1"}],
        [{"key": "Compact"}],
        [{"key": "compact", "value": 1}],
        [{"key": "compact", "extra": True}],
        [{"key": "compact"}, {"key": "compact", "value": "2"}],
    ],
)
def test_read_showcase_tags_rejects_malformed_answers(answer: JSONValue) -> None:
    with pytest.raises(ValidationError):
        read_showcase_tags(answer)
