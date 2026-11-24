"""Browser session snapshot used by server-rendered pages."""

from litestar import get

from squid.api.contract import ANONYMOUS, WEB, browser_only, contract
from squid.api.dependencies import CurrentCaller, Services
from squid.api.private_responses import private_response
from squid.api.v1.browser_views import BrowserViews, SessionSnapshot


@get("/session", operation_id="browser_session_get", security=[ANONYMOUS, WEB], opt=contract(cli=browser_only()))
@private_response
async def session_snapshot(caller: CurrentCaller, services: Services) -> SessionSnapshot:
    """Return user, global permissions and header counts for the current browser."""
    return await BrowserViews(services, caller).session()
