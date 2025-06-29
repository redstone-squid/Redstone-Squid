"""Simple FastAPI server to generate verification codes for users."""

import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from squid.db import DatabaseManager

_db: DatabaseManager | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _db
    _db = DatabaseManager()
    yield


app = FastAPI(lifespan=lifespan)


async def get_db():
    assert _db is not None, "DatabaseManager should be initialized at app startup"
    return _db


class User(BaseModel):
    """A user model."""

    uuid: UUID


@app.post("/verify", status_code=201)
async def get_verification_code(
    user: User, authorization: Annotated[str, Header()], db: Annotated[DatabaseManager, Depends(get_db)]
) -> int:
    """Generate a verification code for a user."""
    if authorization != os.environ["SYNERGY_SECRET"]:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        return await db.user.generate_verification_code(user.uuid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def main() -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("API_PORT", 8000)), log_level="info")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
