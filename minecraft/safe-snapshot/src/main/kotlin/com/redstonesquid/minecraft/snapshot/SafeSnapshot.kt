package com.redstonesquid.minecraft.snapshot

private val resourceIdPattern = Regex("[a-z0-9_.-]+:[a-z0-9_./-]+")
private val propertyNamePattern = Regex("[a-z0-9_]+")
private val propertyValuePattern = Regex("[a-z0-9_.-]+")

@JvmInline
public value class ResourceId private constructor(public val value: String) : Comparable<ResourceId> {
    public val namespace: String
        get() = value.substringBefore(':')

    public val path: String
        get() = value.substringAfter(':')

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

public data class SafeBlockState(
    public val id: ResourceId,
    public val properties: Map<String, String> = emptyMap(),
) {
    init {
        require(id.namespace == VANILLA_NAMESPACE) { "built-in capture only accepts vanilla blocks" }
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
        public val AIR: SafeBlockState = SafeBlockState(ResourceId.parse("minecraft:air"))
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

public data class CaptureDisclosure(
    public val includeInventories: Boolean = true,
    public val includeFreeText: Boolean = true,
)

public data class SnapshotMetadata(
    public val name: String? = null,
    public val completeness: CaptureCompleteness,
    public val disclosure: CaptureDisclosure = CaptureDisclosure(),
) {
    init {
        require(name == null || name.isNotBlank()) { "metadata name must not be blank" }
        require(disclosure.includeFreeText || name == null) { "snapshot name is free text and was not disclosed" }
    }
}

/** A built-in, already-redacted capture. It is not an arbitrary-file sanitizer. */
public data class SafeSnapshot(
    public val dataVersion: Int,
    public val dimensions: Dimensions,
    public val offset: BlockPosition = BlockPosition(0, 0, 0),
    /** Sponge order: `x + z * width + y * width * length`. */
    public val blocks: List<SafeBlockState>,
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
        require(blockEntities.all { it.id.namespace == VANILLA_NAMESPACE }) {
            "built-in capture only accepts vanilla block entities"
        }
        require(entities.all { it.id.namespace == VANILLA_NAMESPACE }) {
            "built-in capture only accepts vanilla entities"
        }
        require(entities.none { it.id == PLAYER_RESOURCE_ID }) { "player entities are never safe to capture" }
        require(entities.all { containsEntityPosition(it.position) }) { "entity position is outside the snapshot" }

        blockEntities.forEach { SafeNbtPolicy.requireSafe(it.data, metadata.disclosure) }
        entities.forEach { SafeNbtPolicy.requireSafe(it.data, metadata.disclosure) }
    }

    private fun containsEntityPosition(position: EntityPosition): Boolean =
        position.x >= 0.0 && position.x < dimensions.width.toDouble() &&
            position.y >= 0.0 && position.y < dimensions.height.toDouble() &&
            position.z >= 0.0 && position.z < dimensions.length.toDouble()
}

public object SafeNbtPolicy {
    private val inventoryKeys = setOf("items", "inventory", "item")
    private val freeTextKeys = setOf(
        "author",
        "back_text",
        "customname",
        "front_text",
        "messages",
        "name",
        "pages",
        "text",
        "title",
    )
    private val forbiddenKeys = setOf(
        "command",
        "dimension",
        "lastoutput",
        "owner",
        "owneruuid",
        "pos",
        "position",
        "world",
        "worlduuid",
        "x",
        "y",
        "z",
    )

    public fun requireSafe(value: NbtValue, disclosure: CaptureDisclosure) {
        visit(value, disclosure, "Data")
    }

    private fun visit(value: NbtValue, disclosure: CaptureDisclosure, path: String) {
        when (value) {
            is NbtCompound -> value.values.forEach { (key, child) ->
                val normalizedKey = key.lowercase()
                require(normalizedKey !in forbiddenKeys && "uuid" !in normalizedKey) {
                    "$path.$key is never allowed in a safe snapshot"
                }
                require(disclosure.includeInventories || normalizedKey !in inventoryKeys) {
                    "$path.$key contains inventory data that was not disclosed"
                }
                require(disclosure.includeFreeText || normalizedKey !in freeTextKeys) {
                    "$path.$key contains free text that was not disclosed"
                }
                visit(child, disclosure, "$path.$key")
            }

            is NbtList -> value.values.forEachIndexed { index, child -> visit(child, disclosure, "$path[$index]") }
            else -> Unit
        }
    }
}

private const val VANILLA_NAMESPACE = "minecraft"
private val PLAYER_RESOURCE_ID = ResourceId.parse("minecraft:player")
