"""The published privacy notice, readable anonymously so it can be shown before an account exists."""

from fastapi import APIRouter, Request, Response

from squid.accounts.domain import CURRENT_CONSENT_VERSION, PRIVACY_NOTICE, PRIVACY_NOTICE_TITLE
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.v1.schemas.consent import PrivacyNoticeDetail
from squid.core.i18n import localization_for, tr
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
    """Return the notice a client must display before recording consent to it, in the negotiated locale.

    The text is the same msgid the Discord prompt shows, so a consent version names one text regardless of
    where it was read.
    """
    locale = locale_for_request(request)
    # Cacheable per version, but negotiated per language, so caches must key on Accept-Language too.
    response.headers["Vary"] = "Accept-Language"
    response.headers["Cache-Control"] = "public, max-age=300"
    with localization_scope(localization_for(locale)):
        return PrivacyNoticeDetail(
            version=CURRENT_CONSENT_VERSION,
            locale=locale,
            title=tr(PRIVACY_NOTICE_TITLE),
            body=tr(PRIVACY_NOTICE),
        )
