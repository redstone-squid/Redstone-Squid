"""Profile field normalization and link validation."""

import pytest

from squid.accounts.domain import (
    MAX_BIO_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_PROFILE_LINKS,
    UNSET,
    AccountProfile,
    ProfileLink,
    ProfileUpdate,
)
from squid.core.errors import ValidationError


class TestTextNormalization:
    def test_display_name_is_nfkc_folded_and_trimmed(self) -> None:
        update = ProfileUpdate(display_name="  ﬁsh  ").validated()
        assert update.display_name == "fish"

    def test_blank_text_clears_the_field(self) -> None:
        assert ProfileUpdate(bio="   ").validated().bio is None

    def test_overlong_display_name_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(display_name="a" * (MAX_DISPLAY_NAME_LENGTH + 1)).validated()

    def test_length_is_measured_after_normalization(self) -> None:
        # NFKC expands the ligature, so a value that fits before folding may not fit after.
        with pytest.raises(ValidationError):
            ProfileUpdate(display_name="ﬁ" * MAX_DISPLAY_NAME_LENGTH).validated()

    def test_bio_keeps_newlines_but_not_control_characters(self) -> None:
        assert ProfileUpdate(bio="one\ntwo").validated().bio == "one\ntwo"
        with pytest.raises(ValidationError):
            ProfileUpdate(bio="one\x07two").validated()

    def test_display_name_refuses_newlines(self) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(display_name="one\ntwo").validated()

    def test_bio_length_limit_applies(self) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(bio="b" * (MAX_BIO_LENGTH + 1)).validated()


class TestLinks:
    def test_https_link_survives(self) -> None:
        link = ProfileLink.parse("YouTube", "https://youtube.com/@someone")
        assert link == ProfileLink(label="YouTube", url="https://youtube.com/@someone")

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com",
            "javascript:alert(1)",
            "data:text/html,<script>",
            "ftp://example.com",
            "https://",
        ],
    )
    def test_non_https_and_hostless_urls_are_refused(self, url: str) -> None:
        with pytest.raises(ValidationError):
            ProfileLink.parse("Site", url)

    def test_credentials_in_the_authority_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            ProfileLink.parse("Site", "https://youtube.com@evil.example/path")

    def test_label_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ProfileLink.parse("   ", "https://example.com")

    def test_link_count_is_capped(self) -> None:
        links = tuple(ProfileLink(f"n{i}", f"https://example.com/{i}") for i in range(MAX_PROFILE_LINKS + 1))
        with pytest.raises(ValidationError):
            ProfileUpdate(links=links).validated()

    def test_links_are_revalidated_through_the_update(self) -> None:
        with pytest.raises(ValidationError):
            ProfileUpdate(links=(ProfileLink("Site", "http://example.com"),)).validated()


class TestPatchSemantics:
    def test_absent_fields_are_left_alone_and_null_clears(self) -> None:
        profile = AccountProfile(account_id=1, display_name="Kept", bio="Gone")
        applied = ProfileUpdate(bio=None).validated().apply(profile)
        assert applied.display_name == "Kept"
        assert applied.bio is None

    def test_empty_update_changes_nothing(self) -> None:
        assert ProfileUpdate().is_empty
        assert not ProfileUpdate(hidden=False).is_empty

    def test_unset_is_not_confused_with_none(self) -> None:
        assert ProfileUpdate().display_name is UNSET
        assert ProfileUpdate(display_name=None).display_name is None

    def test_hidden_flag_applies(self) -> None:
        profile = AccountProfile(account_id=1)
        assert ProfileUpdate(hidden=True).validated().apply(profile).hidden
