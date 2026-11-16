"""The unified UI router preserves typed address validation."""

from typing import Literal
from uuid import UUID

import pytest

from squid_ui.routing import InvalidAddress, Router

router = Router[object]()


@router.route("/builds")
async def index(request: object) -> None: ...


@router.route("/builds/{build_id}")
async def detail(request: object, build_id: int) -> None: ...


@router.route("/drafts")
async def drafts(request: object, inbox: bool = False) -> None: ...


@router.route("/drafts/{draft_id}")
async def draft(request: object, draft_id: UUID) -> None: ...


@router.route("/account", fragment="tab")
async def account(request: object, tab: Literal["claims", "identity"] | None = None) -> None: ...


@pytest.mark.parametrize(
    "address",
    [
        "builds",
        "https://example.org/builds",
        "//example.org/builds",
        "/builds/0",
        "/builds/-1",
        "/builds/abc",
        "/builds/1/",
        "/builds#",
        "/builds?other=1",
        "/drafts/not-a-uuid",
        "/drafts?inbox=1",
        "/drafts?inbox=true&inbox=false",
        "/account#missing",
        "/account#",
        "/account#claims#identity",
        "/account#%FF",
        "/account#%zz",
        "/account?tab=claims",
        "/account?tab",
        "/account?tab=",
        "/builds\n",
        "/%62uilds",
        "/unknown",
    ],
)
def test_invalid_addresses(address: str) -> None:
    with pytest.raises(InvalidAddress):
        router.resolve(address)


def test_typed_address_roundtrips() -> None:
    assert detail.address(12) == "/builds/12"
    assert drafts.address(inbox=True) == "/drafts?inbox=true"
    assert account.address(tab="claims") == "/account#claims"
    assert router.resolve("/account").arguments == {"tab": None}
    assert router.resolve(draft.address(UUID(int=1))).arguments == {"draft_id": UUID(int=1)}
    with pytest.raises(InvalidAddress):
        detail.address(build_id=True)
