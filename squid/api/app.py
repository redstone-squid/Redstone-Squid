"""Simple FastAPI server to generate verification codes for users."""

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from pydantic import BaseModel

from squid.api.errors import register_exception_handlers
from squid.bootstrap import create_application_runtime
from squid.core.errors import AuthenticationError
from squid.logging_config import configure_api_logging
from squid.runtime import ApplicationRuntime, ApplicationServices

RuntimeFactory = Callable[[], ApplicationRuntime]


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def get_services(request: Request) -> ApplicationServices:
    """Return application services initialized during API startup."""
    runtime = cast(ApplicationRuntime, request.app.state.runtime)
    return runtime.services


class User(BaseModel):
    """A user model."""

    uuid: UUID


@router.post("/verify", status_code=201)
async def get_verification_code(
    user: User,
    authorization: Annotated[str, Header()],
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> int:
    """Generate a verification code for a user."""
    if authorization != os.environ["SYNERGY_SECRET"]:
        raise AuthenticationError

    return await services.users.generate_verification_code(user.uuid)


def create_api_app(runtime_factory: RuntimeFactory = create_application_runtime) -> FastAPI:
    """Create an API application with an explicitly owned runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with runtime_factory() as runtime:
            app.state.runtime = runtime
            yield

    api = FastAPI(lifespan=lifespan)
    register_exception_handlers(api)
    api.include_router(router)
    return api


app = create_api_app()


def main() -> None:
    """Run the FastAPI server."""
    import uvicorn

    configure_api_logging()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("API_PORT", 8000)), log_config=None)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
