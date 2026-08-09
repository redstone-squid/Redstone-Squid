"""FastAPI dependencies shared by versioned routes."""

from typing import Annotated, cast

from fastapi import Depends, Query, Request

from squid.api.security import Principal, current_principal
from squid.core.pagination import SignedCursor
from squid.runtime import ApiServices, ApplicationRuntime


async def get_services(request: Request) -> ApiServices:
    """Return application services initialized during API startup."""
    runtime = cast(ApplicationRuntime[ApiServices], request.app.state.runtime)
    return runtime.services


async def cursor_signer(request: Request) -> SignedCursor:
    """Return a collection cursor signer using shared runtime configuration."""
    config = request.app.state.config
    return SignedCursor(config.runtime.cursor_secret.get_secret_value().encode())


PageSize = Annotated[int, Query(ge=1, le=50)]
Services = Annotated[ApiServices, Depends(get_services)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
CursorSigner = Annotated[SignedCursor, Depends(cursor_signer)]
