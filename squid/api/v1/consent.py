"""The published privacy notice.

The notice is public so clients can display it before the user signs in or
creates an account.
"""

from fastapi import APIRouter, Request, Response

from squid.accounts.domain import CURRENT_CONSENT_VERSION, PRIVACY_NOTICE, PRIVACY_NOTICE_TITLE
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.v1.schemas.consent import PrivacyNoticeDetail
from squid.core.i18n import localization_for, tr_now
from squid_ui.text import localization_scope

router = APIRouter(prefix="/consent", tags=["users"])


@router.get(
    "/notice",
    response_model=PrivacyNoticeDetail,
    responses=responses(503),
    operation_id="consent_notice_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def get_notice(request: Request, response: Response) -> PrivacyNoticeDetail:
    """Return the privacy notice in the negotiated language.

    Clients should display this text and submit its version when recording consent.
    The API and Discord use the same notice text and version.
    """
    locale = locale_for_request(request)
    # The notice changes only when its version does, but it is negotiated per language, so the
    # cache has to key on that too.
    response.headers["Vary"] = "Accept-Language"
    response.headers["Cache-Control"] = "public, max-age=300"
    with localization_scope(localization_for(locale)):
        return PrivacyNoticeDetail(
            version=CURRENT_CONSENT_VERSION,
            locale=locale,
            title=tr_now(PRIVACY_NOTICE_TITLE),
            body=tr_now(PRIVACY_NOTICE),
        )
