"""The published privacy notice.

The notice is public so clients can display it before the user signs in or
creates an account.
"""

from litestar import Request, Response, Router, get

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.errors import responses
from squid.api.i18n import locale_for_request
from squid.api.v1.schemas.privacy import PrivacyNoticeDetail
from squid.core.accounts.domain import PRIVACY_NOTICE, PRIVACY_NOTICE_TITLE, PRIVACY_NOTICE_VERSION
from squid.core.i18n import localization_for
from squid_i18n import localization_scope


@get(
    "/policy",
    responses=responses(503),
    operation_id="privacy_policy_get",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
    status_code=200,
)
async def get_notice(request: Request) -> Response[PrivacyNoticeDetail]:
    """Return the privacy notice in the negotiated language.

    The API and Discord use the same notice text and version.
    """
    response_headers: dict[str, str] = {}
    locale = locale_for_request(request)
    response_headers["Vary"] = "Accept-Language"
    response_headers["Cache-Control"] = "public, max-age=300"
    with localization_scope(localization_for(locale)):
        return Response(
            content=PrivacyNoticeDetail(
                version=PRIVACY_NOTICE_VERSION,
                locale=locale,
                title=PRIVACY_NOTICE_TITLE.resolve(),
                body=PRIVACY_NOTICE.resolve(),
            ),
            headers=response_headers,
            status_code=200,
        )


router = Router(path="/privacy", tags=["accounts"], route_handlers=[get_notice])
