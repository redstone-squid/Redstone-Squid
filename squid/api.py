"""Simple FastAPI server to generate verification codes for users."""

import os
import random
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from postgrest.types import ReturnMethod
from pydantic import BaseModel

from squid.db import DatabaseManager
from squid.db.user import get_minecraft_username
from squid.db.utils import utcnow

app = FastAPI()


class User(BaseModel):
    """A user model."""

    uuid: UUID


@app.post("/verify", status_code=201)
async def get_verification_code(user: User, authorization: Annotated[str, Header()]) -> int:
    """Generate a verification code for a user."""
    if authorization != os.environ["SYNERGY_SECRET"]:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if (username := await get_minecraft_username(user.uuid)) is None:
        raise HTTPException(status_code=400, detail="Invalid user")

    db = DatabaseManager()
    # Invalidate existing codes for this user
    await (
        db.table("verification_codes")
        .update({"valid": False}, returning=ReturnMethod.minimal)
        .eq("minecraft_uuid", str(user.uuid))
        .gt("expires", utcnow())
        .execute()
    )

    code = random.randint(100000, 999999)
    await (
        db.table("verification_codes")
        .insert({"minecraft_uuid": str(user.uuid), "username": username, "code": code}, returning=ReturnMethod.minimal)
        .execute()
    )
    return code


def main() -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("API_PORT", 8000)), log_level="info")


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    main()
