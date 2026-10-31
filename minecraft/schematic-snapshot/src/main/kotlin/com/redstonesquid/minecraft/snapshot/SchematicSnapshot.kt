package com.redstonesquid.minecraft.snapshot

private val resourceIdPattern = Regex("[a-z0-9_.-]+:[a-z0-9_./-]+")
private val propertyNamePattern = Regex("[a-z0-9_]+")
private val propertyValuePattern = Regex("[a-z0-9_.-]+")

@JvmInline
public value class ResourceId private constructor(public val value: String) : Comparable<ResourceId> {
    override fun compareTo(other: ResourceId): Int = value.compareTo(other.value)

    override fun toString(): String = value

    public companion object {
        public fun parse(value: String): ResourceId {
            val canonical = if (':' in value) value else "minecraft:$value"
            require(resourceIdPattern.matches(canonical)) { "invalid resource id: $value" }
            return ResourceId(canonical)
        }
    }
}

public data class BlockStateSnapshot(
    public val id: ResourceId,
    public val properties: Map<String, String> = emptyMap(),
) {
    init {
        require(properties.keys.all(propertyNamePattern::matches)) { "block state contains an invalid property name" }
        require(properties.values.all(propertyValuePattern::matches)) {
            "block state contains an invalid property value"
        }
    }

    private val sortedProperties: Map<String, String> = properties.toSortedMap()

    public val canonicalName: String
        get() = if (sortedProperties.isEmpty()) {
            id.value
        } else {
            sortedProperties.entries.joinToString(separator = ",", prefix = "${id.value}[", postfix = "]") {
                "${it.key}=${it.value}"
            }
        }

    public companion object {
        public val AIR: BlockStateSnapshot = BlockStateSnapshot(ResourceId.parse("minecraft:air"))
    }
}

public data class BlockEntitySnapshot(
    public val position: BlockPosition,
    public val id: ResourceId,
    public val data: NbtCompound = NbtCompound.of(),
)

public data class EntityPosition(
    public val x: Double,
    public val y: Double,
    public val z: Double,
) {
    init {
        require(x.isFinite() && y.isFinite() && z.isFinite()) { "entity position must be finite" }
    }
}

public data class EntitySnapshot(
    public val position: EntityPosition,
    public val id: ResourceId,
    public val data: NbtCompound = NbtCompound.of(),
)

public enum class CaptureCompleteness(public val protocolValue: String) {
    SERVER_AUTHORITATIVE("server_authoritative"),
    CLIENT_VISIBLE("client_visible"),
}

public data class SnapshotMetadata(
    public val name: String? = null,
    public val completeness: CaptureCompleteness,
) {
    init {
        require(name == null || name.isNotBlank()) { "metadata name must not be blank" }
    }
}

/** A structurally validated capture that preserves block and entity data. */
public data class SchematicSnapshot(
    public val dataVersion: Int,
    public val dimensions: Dimensions,
    public val offset: BlockPosition = BlockPosition(0, 0, 0),
    /** Sponge order: `x + z * width + y * width * length`. */
    public val blocks: List<BlockStateSnapshot>,
    public val blockEntities: List<BlockEntitySnapshot> = emptyList(),
    public val entities: List<EntitySnapshot> = emptyList(),
    public val metadata: SnapshotMetadata,
) {
    init {
        require(dataVersion >= 0) { "dataVersion must not be negative" }
        require(dimensions.volume <= Int.MAX_VALUE) { "snapshot block list cannot exceed integer indexing" }
        require(blocks.size == dimensions.volume.toInt()) { "blocks must contain exactly dimensions.volume entries" }
        require(blockEntities.all { dimensions.contains(it.position) }) {
            "block entity position is outside the snapshot"
        }
        require(blockEntities.map(BlockEntitySnapshot::position).distinct().size == blockEntities.size) {
            "block entity positions must be unique"
        }
        require(entities.all { containsEntityPosition(it.position) }) { "entity position is outside the snapshot" }
    }

    private fun containsEntityPosition(position: EntityPosition): Boolean =
        position.x >= 0.0 && position.x < dimensions.width.toDouble() &&
            position.y >= 0.0 && position.y < dimensions.height.toDouble() &&
            position.z >= 0.0 && position.z < dimensions.length.toDouble()
}
