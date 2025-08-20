from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.db.domain import VerificationCode
from squid.db.schema import OrmVerificationCode


class VerificationCodeRepository:
    """Repository for VerificationCode persistence."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, verification_code: VerificationCode) -> None:
        """Add a verification code to the database."""
        orm_verification_code = OrmVerificationCode.from_domain(verification_code)
        self.session.add(orm_verification_code)
        await self.session.flush()

    async def get_by_code(self, code: str) -> VerificationCode | None:
        """Get a verification code by the code.

        Args:
            code: The verification code.

        Returns:
            The verification code if found, None otherwise.
        """
        stmt = select(OrmVerificationCode).where(OrmVerificationCode.code == code)
        result = await self.session.execute(stmt)
        orm_verification_code = result.scalar_one_or_none()
        if orm_verification_code is None:
            return None
        return orm_verification_code.to_domain()
