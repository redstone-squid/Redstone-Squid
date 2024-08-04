"""Simple FastAPI server to generate verification codes for users."""
import os
import random

from fastapi import FastAPI, HTTPException

from database.database import DatabaseManager

app = FastAPI()


@app.get("/verify")
async def get_verification_code(uuid: str, super_duper_secret: str) -> int:
    """Generate a verification code for a user."""
    if super_duper_secret != os.environ["SYNERGY_SECRET"]:
        raise HTTPException(status_code=401, detail="Unauthorized")

    code = random.randint(100000, 999999)
    await DatabaseManager().table("verification_codes").insert({"minecraft_uuid": uuid, "code": code}).execute()
    return code


if __name__ == "__main__":
    import asyncio
    import uvicorn
    from dotenv import load_dotenv
    load_dotenv()

    asyncio.run(DatabaseManager.setup())
    uvicorn.run(app, host="0.0.0.0", port=3000)
