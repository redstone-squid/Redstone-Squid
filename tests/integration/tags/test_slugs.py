"""Tag URLs retain their owner through renames, collisions, merges, and retirement."""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def test_reserved_slugs(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with migrated_session_factory.begin() as session:
        insert = text("""
            INSERT INTO tag_definitions (stable_key, display_name, normalized_name, authority, semantic_kind,
                                         value_type, moderation_status, render_template, default_display_order)
            VALUES (:key, 'Slug Test', 'slug test', 'user', 'showcase', 'none', 'approved', '{name}', 0)
            RETURNING id, slug
        """)
        first, plain = (await session.execute(insert, {"key": "slug_test_first"})).one()
        second, suffixed = (await session.execute(insert, {"key": "slug_test_second"})).one()
        assert plain == "slug-test"
        assert re.fullmatch(r"slug-test-[a-z0-9]{5}", suffixed)

        rename = text("UPDATE tag_definitions SET slug = :slug WHERE id = :id")
        await session.execute(rename, {"id": first, "slug": "renamed"})
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(rename, {"id": second, "slug": plain})
        await session.execute(rename, {"id": first, "slug": plain})

        # A merge transfers all URL reservations before removing the source in the same transaction.
        await session.execute(
            text("UPDATE tag_slugs SET tag_id = :dst WHERE tag_id = :src"), {"src": first, "dst": second}
        )
        await session.execute(text("DELETE FROM tag_definitions WHERE id = :id"), {"id": first})
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        owner = text("SELECT tag_id FROM tag_slugs WHERE slug = :slug")
        assert await session.scalar(owner, {"slug": "renamed"}) == second
        await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        await session.execute(text("DELETE FROM tag_definitions WHERE id = :id"), {"id": second})
        assert (await session.execute(owner, {"slug": plain})).scalar_one() is None
        _, replacement = (await session.execute(insert, {"key": "slug_test_third"})).one()
        assert replacement != plain
