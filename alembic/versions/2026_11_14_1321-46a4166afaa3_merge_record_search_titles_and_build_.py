"""merge record search titles and build credits

Revision ID: 46a4166afaa3
Revises: a91c34e8b672, e1c4b7a0d3f6
Create Date: 2026-11-14 13:21:16.293423+00:00
"""

from collections.abc import Sequence

revision: str = "46a4166afaa3"
down_revision: str | Sequence[str] | None = ("a91c34e8b672", "e1c4b7a0d3f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    pass


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    pass
