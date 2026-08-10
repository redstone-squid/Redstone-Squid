package com.redstonesquid.minecraft.snapshot

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class SafeSnapshotTest {
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
        val state = SafeBlockState(
            ResourceId.parse("repeater"),
            mapOf("powered" to "false", "delay" to "2", "facing" to "north"),
        )

        assertEquals("minecraft:repeater[delay=2,facing=north,powered=false]", state.canonicalName)
    }

    @Test
    fun `snapshot rejects identity and command data recursively`() {
        val dangerousData = NbtCompound.of(
            "nested" to NbtCompound.of("OwnerUUID" to NbtString("private")),
        )

        val error = assertFailsWith<IllegalArgumentException> {
            minimalSnapshot(blockEntityData = dangerousData)
        }
        assertTrue(error.message.orEmpty().contains("OwnerUUID"))

        assertFailsWith<IllegalArgumentException> {
            minimalSnapshot(
                entities = listOf(EntitySnapshot(EntityPosition(0.5, 0.0, 0.5), ResourceId.parse("player"))),
            )
        }
    }

    @Test
    fun `disclosure off rejects inventories and free text`() {
        assertFailsWith<IllegalArgumentException> {
            minimalSnapshot(
                disclosure = CaptureDisclosure(includeInventories = false, includeFreeText = true),
                blockEntityData = NbtCompound.of("Items" to NbtList.of(emptyList())),
            )
        }
        assertFailsWith<IllegalArgumentException> {
            minimalSnapshot(
                disclosure = CaptureDisclosure(includeInventories = true, includeFreeText = false),
                blockEntityData = NbtCompound.of("CustomName" to NbtString("secret")),
            )
        }
    }

    private fun minimalSnapshot(
        disclosure: CaptureDisclosure = CaptureDisclosure(),
        blockEntityData: NbtCompound = NbtCompound.of(),
        entities: List<EntitySnapshot> = emptyList(),
    ): SafeSnapshot = SafeSnapshot(
        dataVersion = 1,
        dimensions = Dimensions(1, 1, 1),
        blocks = listOf(SafeBlockState.AIR),
        blockEntities = if (blockEntityData.isEmpty) {
            emptyList()
        } else {
            listOf(BlockEntitySnapshot(BlockPosition(0, 0, 0), ResourceId.parse("chest"), blockEntityData))
        },
        entities = entities,
        metadata = SnapshotMetadata(completeness = CaptureCompleteness.SERVER_AUTHORITATIVE, disclosure = disclosure),
    )
}
