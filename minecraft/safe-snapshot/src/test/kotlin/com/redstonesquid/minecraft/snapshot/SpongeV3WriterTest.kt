package com.redstonesquid.minecraft.snapshot

import java.security.MessageDigest
import java.util.Base64
import java.util.zip.GZIPInputStream
import org.junit.jupiter.api.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class SpongeV3WriterTest {
    @Test
    fun `writer is byte deterministic and uses fixed gzip metadata`() {
        val first = SpongeV3Writer.write(goldenSnapshot())
        val second = SpongeV3Writer.write(goldenSnapshot())

        assertContentEquals(first, second)
        assertContentEquals(byteArrayOf(0, 0, 0, 0), first.copyOfRange(4, 8))
        assertEquals(0xFF.toByte(), first[9])
    }

    @Test
    fun `writer output matches reviewed golden fixture`() {
        val actual = SpongeV3Writer.write(goldenSnapshot())
        val expectedBase64 = requireNotNull(javaClass.getResource("/golden/minimal.schem.base64"))
            .readText()
            .filterNot(Char::isWhitespace)

        assertEquals(expectedBase64, Base64.getEncoder().encodeToString(actual))
        assertEquals(
            requireNotNull(javaClass.getResource("/golden/minimal.schem.sha256")).readText().trim(),
            MessageDigest.getInstance("SHA-256").digest(actual).joinToString("") { "%02x".format(it) },
        )
    }

    @Test
    fun `golden payload is a nested Sponge v3 NBT document`() {
        val uncompressed = GZIPInputStream(SpongeV3Writer.write(goldenSnapshot()).inputStream()).readAllBytes()
        val latin1 = uncompressed.toString(Charsets.ISO_8859_1)

        assertEquals(NbtType.COMPOUND.id.toByte(), uncompressed.first())
        assertTrue(latin1.contains("Schematic"))
        assertTrue(latin1.contains("Version"))
        assertTrue(latin1.contains("BlockEntities"))
        assertTrue(latin1.contains("minecraft:repeater[delay=2,facing=north,powered=false]"))
    }

    private fun goldenSnapshot(): SafeSnapshot = SafeSnapshot(
        dataVersion = 5000,
        dimensions = Dimensions(2, 1, 2),
        offset = BlockPosition(-1, 0, 3),
        blocks = listOf(
            SafeBlockState.AIR,
            SafeBlockState(
                ResourceId.parse("minecraft:repeater"),
                mapOf("powered" to "false", "facing" to "north", "delay" to "2"),
            ),
            SafeBlockState(ResourceId.parse("minecraft:redstone_wire"), mapOf("power" to "15")),
            SafeBlockState.AIR,
        ),
        blockEntities = listOf(
            BlockEntitySnapshot(
                BlockPosition(0, 0, 0),
                ResourceId.parse("minecraft:chest"),
                NbtCompound.of("CustomName" to NbtString("Demo")),
            ),
        ),
        entities = listOf(
            EntitySnapshot(
                EntityPosition(1.5, 0.0, 0.5),
                ResourceId.parse("minecraft:armor_stand"),
                NbtCompound.of("Invisible" to NbtByte(1)),
            ),
        ),
        metadata = SnapshotMetadata(
            name = "Golden",
            completeness = CaptureCompleteness.SERVER_AUTHORITATIVE,
        ),
    )
}
