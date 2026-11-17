"""Pin which staging options guarantee a drawn candidate."""

from typing import assert_type

from squid_ui.ext.discord.messages.candidates import _ApplicationCandidate, _Candidate
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.target_types import ComponentsV2Target


async def staging(root: MessageRoot[ComponentsV2Target], preflight: bool, disabled: bool) -> None:
    assert_type(await root._stage_loaded(), _Candidate[ComponentsV2Target])
    assert_type(await root._stage_loaded(disabled=disabled), _Candidate[ComponentsV2Target])
    assert_type(await root._stage_loaded(preflight=False), _Candidate[ComponentsV2Target])
    assert_type(await root._stage_loaded(disabled=True, preflight=preflight), _Candidate[ComponentsV2Target])
    assert_type(await root._stage_loaded(preflight=True), _ApplicationCandidate[ComponentsV2Target])
    assert_type(await root._stage_loaded(preflight=preflight), _ApplicationCandidate[ComponentsV2Target])
