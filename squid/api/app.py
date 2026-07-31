"""Simple FastAPI server to generate verification codes for users."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from pydantic import BaseModel

from squid.api.errors import register_exception_handlers
from squid.bootstrap import create_application_runtime
from squid.config import ApiProcessConfig, RuntimeConfig, load_api_process_config
from squid.core.errors import AuthenticationError
from squid.logging_config import configure_api_logging
from squid.runtime import ApplicationRuntime, ApplicationServices

RuntimeFactory = Callable[[RuntimeConfig], ApplicationRuntime]
ConfigFactory = Callable[[], ApiProcessConfig]


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
    request: Request,
    services: Annotated[ApplicationServices, Depends(get_services)],
) -> int:
    """Generate a verification code for a user."""
    config = cast(ApiProcessConfig, request.app.state.config)
    if authorization != config.api.secret.get_secret_value():
        raise AuthenticationError

    return await services.users.generate_verification_code(user.uuid)


def create_api_app(
    runtime_factory: RuntimeFactory = create_application_runtime,
    *,
    config: ApiProcessConfig | None = None,
    config_factory: ConfigFactory = load_api_process_config,
) -> FastAPI:
    """Create an API application with an explicitly owned runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_config = config or config_factory()
        app.state.config = resolved_config
        async with runtime_factory(resolved_config.runtime) as runtime:
            app.state.runtime = runtime
            yield

    api = FastAPI(lifespan=lifespan)
    register_exception_handlers(api)
    api.include_router(router)
    return api


app = create_api_app()


def main(process_config: ApiProcessConfig | None = None) -> None:
    """Run the FastAPI server."""
    import uvicorn

    resolved_config = process_config or load_api_process_config()
    configure_api_logging(resolved_config.logging)
    uvicorn.run(
        create_api_app(config=resolved_config),
        host="0.0.0.0",
        port=resolved_config.api.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
