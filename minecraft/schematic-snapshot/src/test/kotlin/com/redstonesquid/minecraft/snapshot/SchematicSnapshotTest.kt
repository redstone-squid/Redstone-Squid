package com.redstonesquid.minecraft.snapshot

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class SchematicSnapshotTest {
    @Test
    fun `selection is normalized and checked without integer overflow`() {
        val selection = CuboidSelection.fromCorners(
            BlockPosition(12, 70, -4),
            BlockPosition(10, 69, -1),
        )

        assertEquals(BlockPosition(10, 69, -4), selection.minimum)
        assertEquals(Dimensions(3, 2, 4), selection.dimensions)
        assertEquals(BlockPosition(2, 1, 0), selection.toLocal(BlockPosition(12, 70, -4)))

        assertFailsWith<IllegalArgumentException> {
            CuboidSelection.fromCorners(
                BlockPosition(Int.MIN_VALUE, 0, 0),
                BlockPosition(Int.MAX_VALUE, 0, 0),
            )
        }
    }

    @Test
    fun `balanced capture budget reports every violated dimension`() {
        val violations = CaptureBudget().violations(Dimensions(513, 512, 77))
        assertEquals(listOf(CaptureBudgetKind.WIDTH, CaptureBudgetKind.VOLUME), violations.map { it.kind })
    }

    @Test
    fun `block states have canonical property order`() {
        val state = BlockStateSnapshot(
            ResourceId.parse("repeater"),
            mapOf("powered" to "false", "delay" to "2", "facing" to "north"),
        )

        assertEquals("minecraft:repeater[delay=2,facing=north,powered=false]", state.canonicalName)
    }

    @Test
    fun `snapshot preserves nested inventories text commands and identity data`() {
        val item = NbtCompound.of(
            "id" to NbtString("minecraft:written_book"),
            "tag" to NbtCompound.of(
                "author" to NbtString("Builder"),
                "pages" to NbtList.of(listOf(NbtString("Build instructions"))),
            ),
        )
        val original = NbtCompound.of(
            "Items" to NbtList.of(listOf(item)),
            "CustomName" to NbtString("Door controller"),
            "Command" to NbtString("say hello"),
            "nested" to NbtCompound.of("OwnerUUID" to NbtString("owner-id")),
        )
        val entity = EntitySnapshot(
            EntityPosition(0.5, 0.0, 0.5),
            ResourceId.parse("example:custom_entity"),
            NbtCompound.of("UUID" to NbtString("entity-id")),
        )
        val snapshot = minimalSnapshot(blockEntityData = original, entities = listOf(entity))

        assertEquals(original, snapshot.blockEntities.single().data)
        assertEquals(entity, snapshot.entities.single())
        val encoded = java.util.zip.GZIPInputStream(SpongeV3Writer.write(snapshot).inputStream())
            .use { it.readAllBytes().toString(Charsets.ISO_8859_1) }
        for (value in listOf("Builder", "Build instructions", "Door controller", "say hello", "owner-id", "entity-id")) {
            assertTrue(encoded.contains(value))
        }
    }

    @Test
    fun `snapshot still rejects out of bounds entity positions`() {
        assertFailsWith<IllegalArgumentException> {
            minimalSnapshot(
                entities = listOf(EntitySnapshot(EntityPosition(1.5, 0.0, 0.5), ResourceId.parse("armor_stand"))),
            )
        }
    }

    private fun minimalSnapshot(
        blockEntityData: NbtCompound = NbtCompound.of(),
        entities: List<EntitySnapshot> = emptyList(),
    ): SchematicSnapshot = SchematicSnapshot(
        dataVersion = 1,
        dimensions = Dimensions(1, 1, 1),
        blocks = listOf(BlockStateSnapshot.AIR),
        blockEntities = if (blockEntityData.isEmpty) {
            emptyList()
        } else {
            listOf(BlockEntitySnapshot(BlockPosition(0, 0, 0), ResourceId.parse("chest"), blockEntityData))
        },
        entities = entities,
        metadata = SnapshotMetadata(completeness = CaptureCompleteness.SERVER_AUTHORITATIVE),
    )
}
