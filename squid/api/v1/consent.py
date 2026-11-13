"""The published privacy notice.

The notice is public so clients can display it before the user signs in or
creates an account.
"""

from litestar import Request, Response, Router, get

from squid.accounts.domain import CURRENT_CONSENT_VERSION, PRIVACY_NOTICE, PRIVACY_NOTICE_TITLE
from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.v1.schemas.consent import PrivacyNoticeDetail
from squid.core.i18n import localization_for
from squid_i18n import localization_scope


@get(
    "/notice",
    responses=responses(503),
    operation_id="consent_notice_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
)
async def get_notice(request: Request) -> Response[PrivacyNoticeDetail]:
    """Return the privacy notice in the negotiated language.

    Clients should display this text and submit its version when recording consent.
    The API and Discord use the same notice text and version.
    """
    response_headers: dict[str, str] = {}
    locale = locale_for_request(request)
    response_headers["Vary"] = "Accept-Language"
    response_headers["Cache-Control"] = "public, max-age=300"
    with localization_scope(localization_for(locale)):
        return Response(
            content=PrivacyNoticeDetail(
                version=CURRENT_CONSENT_VERSION,
                locale=locale,
                title=PRIVACY_NOTICE_TITLE.resolve(),
                body=PRIVACY_NOTICE.resolve(),
            ),
            headers=response_headers,
            status_code=200,
        )


router = Router(path="/consent", tags=["users"], route_handlers=[get_notice])
