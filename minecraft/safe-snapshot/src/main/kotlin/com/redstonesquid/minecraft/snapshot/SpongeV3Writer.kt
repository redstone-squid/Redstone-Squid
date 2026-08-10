package com.redstonesquid.minecraft.snapshot

import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.OutputStream
import java.util.zip.CRC32
import java.util.zip.Deflater

/** Deterministic, write-only encoder for the Sponge Schematic v3 format. */
public object SpongeV3Writer {
    public fun write(snapshot: SafeSnapshot): ByteArray {
        val output = ByteArrayOutputStream()
        write(snapshot, output)
        return output.toByteArray()
    }

    public fun write(snapshot: SafeSnapshot, output: OutputStream) {
        val gzip = DeterministicGzipOutputStream(output)
        val data = DataOutputStream(gzip)
        NbtWriter(data).writeRoot(buildRoot(snapshot))
        data.flush()
        gzip.finish()
    }

    private fun buildRoot(snapshot: SafeSnapshot): NbtCompound {
        val paletteNames = snapshot.blocks.map(SafeBlockState::canonicalName).distinct().sorted()
        val palette = paletteNames.withIndex().associate { (index, name) -> name to NbtInt(index) }
        val paletteIndices = paletteNames.withIndex().associate { (index, name) -> name to index }
        val blockData = encodeVarInts(snapshot.blocks.map { paletteIndices.getValue(it.canonicalName) })

        val blocks = NbtCompound.of(
            "Palette" to NbtCompound.of(palette),
            "Data" to NbtByteArray(blockData),
            "BlockEntities" to NbtList.of(
                snapshot.blockEntities.sortedWith(blockEntityComparator).map(::encodeBlockEntity),
            ),
        )
        val schematic = NbtCompound.of(
            "Version" to NbtInt(SPONGE_VERSION),
            "DataVersion" to NbtInt(snapshot.dataVersion),
            "Metadata" to encodeMetadata(snapshot.metadata),
            "Width" to NbtShort(snapshot.dimensions.width.toShort()),
            "Height" to NbtShort(snapshot.dimensions.height.toShort()),
            "Length" to NbtShort(snapshot.dimensions.length.toShort()),
            "Offset" to NbtIntArray(intArrayOf(snapshot.offset.x, snapshot.offset.y, snapshot.offset.z)),
            "Blocks" to blocks,
            "Entities" to NbtList.of(snapshot.entities.sortedWith(entityComparator).map(::encodeEntity)),
        )
        return NbtCompound.of("Schematic" to schematic)
    }

    private fun encodeMetadata(metadata: SnapshotMetadata): NbtCompound {
        val redstoneSquid = NbtCompound.of(
            "CaptureCompleteness" to NbtString(metadata.completeness.protocolValue),
            "IncludedFreeText" to NbtByte(if (metadata.disclosure.includeFreeText) 1 else 0),
            "IncludedInventories" to NbtByte(if (metadata.disclosure.includeInventories) 1 else 0),
        )
        val fields = buildMap<String, NbtValue> {
            metadata.name?.let { put("Name", NbtString(it)) }
            put("RedstoneSquid", redstoneSquid)
        }
        return NbtCompound.of(fields)
    }

    private fun encodeBlockEntity(blockEntity: BlockEntitySnapshot): NbtValue {
        val fields = buildMap<String, NbtValue> {
            put("Pos", NbtIntArray(intArrayOf(blockEntity.position.x, blockEntity.position.y, blockEntity.position.z)))
            put("Id", NbtString(blockEntity.id.value))
            if (!blockEntity.data.isEmpty) {
                put("Data", blockEntity.data)
            }
        }
        return NbtCompound.of(fields)
    }

    private fun encodeEntity(entity: EntitySnapshot): NbtValue {
        val fields = buildMap<String, NbtValue> {
            put(
                "Pos",
                NbtList.of(
                    listOf(NbtDouble(entity.position.x), NbtDouble(entity.position.y), NbtDouble(entity.position.z)),
                ),
            )
            put("Id", NbtString(entity.id.value))
            if (!entity.data.isEmpty) {
                put("Data", entity.data)
            }
        }
        return NbtCompound.of(fields)
    }

    private fun encodeVarInts(values: List<Int>): ByteArray {
        val output = ByteArrayOutputStream(values.size)
        values.forEach { original ->
            var value = original
            do {
                var byte = value and 0x7F
                value = value ushr 7
                if (value != 0) {
                    byte = byte or 0x80
                }
                output.write(byte)
            } while (value != 0)
        }
        return output.toByteArray()
    }

    private val blockEntityComparator = compareBy<BlockEntitySnapshot>(
        { it.position.y },
        { it.position.z },
        { it.position.x },
        { it.id.value },
    )
    private val entityComparator = compareBy<EntitySnapshot>(
        { it.position.y },
        { it.position.z },
        { it.position.x },
        { it.id.value },
    )

    private const val SPONGE_VERSION = 3
}

private class NbtWriter(private val output: DataOutputStream) {
    fun writeRoot(root: NbtCompound) {
        output.writeByte(NbtType.COMPOUND.id)
        output.writeUTF("")
        writePayload(root)
    }

    private fun writeNamed(name: String, value: NbtValue) {
        require(value.type != NbtType.END) { "named TAG_End is invalid" }
        output.writeByte(value.type.id)
        output.writeUTF(name)
        writePayload(value)
    }

    private fun writePayload(value: NbtValue) {
        when (value) {
            is NbtByte -> output.writeByte(value.value.toInt())
            is NbtShort -> output.writeShort(value.value.toInt())
            is NbtInt -> output.writeInt(value.value)
            is NbtLong -> output.writeLong(value.value)
            is NbtFloat -> output.writeFloat(value.value)
            is NbtDouble -> output.writeDouble(value.value)
            is NbtByteArray -> {
                output.writeInt(value.value.size)
                output.write(value.value)
            }

            is NbtString -> output.writeUTF(value.value)
            is NbtList -> {
                output.writeByte(value.elementType.id)
                output.writeInt(value.values.size)
                value.values.forEach(::writePayload)
            }

            is NbtCompound -> {
                value.values.forEach(::writeNamed)
                output.writeByte(NbtType.END.id)
            }

            is NbtIntArray -> {
                output.writeInt(value.value.size)
                value.value.forEach(output::writeInt)
            }

            is NbtLongArray -> {
                output.writeInt(value.value.size)
                value.value.forEach(output::writeLong)
            }
        }
    }
}

/** Gzip framing with fixed metadata; java.util.zip's raw deflater does the payload work. */
private class DeterministicGzipOutputStream(private val target: OutputStream) : OutputStream() {
    private val crc = CRC32()
    private val deflater = Deflater(Deflater.DEFAULT_COMPRESSION, true)
    private val deflateBuffer = ByteArray(DEFAULT_BUFFER_SIZE)
    private var inputSize: Long = 0
    private var finished = false

    init {
        target.write(GZIP_HEADER)
    }

    override fun write(value: Int) {
        write(byteArrayOf(value.toByte()), 0, 1)
    }

    override fun write(buffer: ByteArray, offset: Int, length: Int) {
        check(!finished) { "gzip stream is finished" }
        require(offset >= 0 && length >= 0 && offset + length <= buffer.size) { "invalid byte range" }
        if (length == 0) {
            return
        }
        crc.update(buffer, offset, length)
        inputSize = (inputSize + length) and UINT_MASK
        deflater.setInput(buffer, offset, length)
        drain()
    }

    fun finish() {
        if (finished) {
            return
        }
        deflater.finish()
        while (!deflater.finished()) {
            val count = deflater.deflate(deflateBuffer)
            target.write(deflateBuffer, 0, count)
        }
        writeLittleEndian(crc.value)
        writeLittleEndian(inputSize)
        target.flush()
        deflater.end()
        finished = true
    }

    private fun drain() {
        while (!deflater.needsInput()) {
            val count = deflater.deflate(deflateBuffer)
            if (count == 0) {
                break
            }
            target.write(deflateBuffer, 0, count)
        }
    }

    private fun writeLittleEndian(value: Long) {
        repeat(Int.SIZE_BYTES) { shift -> target.write((value ushr (shift * Byte.SIZE_BITS)).toInt() and 0xFF) }
    }

    private companion object {
        private const val UINT_MASK: Long = 0xFFFF_FFFFL
        private val GZIP_HEADER = byteArrayOf(
            0x1F,
            0x8B.toByte(),
            Deflater.DEFLATED.toByte(),
            0,
            0,
            0,
            0,
            0,
            0,
            0xFF.toByte(),
        )
    }
}
