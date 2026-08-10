package com.redstonesquid.minecraft.snapshot

public data class BlockPosition(
    public val x: Int,
    public val y: Int,
    public val z: Int,
)

public data class Dimensions(
    public val width: Int,
    public val height: Int,
    public val length: Int,
) {
    init {
        require(width in 1..UNSIGNED_SHORT_MAX) { "width must fit an unsigned short" }
        require(height in 1..UNSIGNED_SHORT_MAX) { "height must fit an unsigned short" }
        require(length in 1..UNSIGNED_SHORT_MAX) { "length must fit an unsigned short" }
    }

    public val volume: Long
        get() = width.toLong() * height.toLong() * length.toLong()

    public fun contains(position: BlockPosition): Boolean =
        position.x in 0 until width && position.y in 0 until height && position.z in 0 until length

    private companion object {
        private const val UNSIGNED_SHORT_MAX: Int = 65_535
    }
}

public data class CuboidSelection private constructor(
    public val minimum: BlockPosition,
    public val maximum: BlockPosition,
    public val dimensions: Dimensions,
) {
    public fun toLocal(worldPosition: BlockPosition): BlockPosition = BlockPosition(
        x = Math.subtractExact(worldPosition.x, minimum.x),
        y = Math.subtractExact(worldPosition.y, minimum.y),
        z = Math.subtractExact(worldPosition.z, minimum.z),
    )

    public companion object {
        public fun fromCorners(first: BlockPosition, second: BlockPosition): CuboidSelection {
            val minimum = BlockPosition(
                x = minOf(first.x, second.x),
                y = minOf(first.y, second.y),
                z = minOf(first.z, second.z),
            )
            val maximum = BlockPosition(
                x = maxOf(first.x, second.x),
                y = maxOf(first.y, second.y),
                z = maxOf(first.z, second.z),
            )
            val dimensions = Dimensions(
                width = inclusiveDistance(minimum.x, maximum.x, "x"),
                height = inclusiveDistance(minimum.y, maximum.y, "y"),
                length = inclusiveDistance(minimum.z, maximum.z, "z"),
            )
            return CuboidSelection(minimum, maximum, dimensions)
        }

        private fun inclusiveDistance(minimum: Int, maximum: Int, axis: String): Int {
            val distance = maximum.toLong() - minimum.toLong() + 1L
            require(distance <= Int.MAX_VALUE) { "$axis selection distance exceeds integer range" }
            return distance.toInt()
        }
    }
}

public enum class CaptureBudgetKind {
    WIDTH,
    HEIGHT,
    LENGTH,
    VOLUME,
}

public data class CaptureBudgetViolation(
    public val kind: CaptureBudgetKind,
    public val actual: Long,
    public val limit: Long,
)

public data class CaptureBudget(
    public val maximumAxis: Int = 512,
    public val maximumVolume: Long = 20_000_000,
) {
    init {
        require(maximumAxis > 0) { "maximumAxis must be positive" }
        require(maximumVolume > 0) { "maximumVolume must be positive" }
    }

    public fun violations(dimensions: Dimensions): List<CaptureBudgetViolation> = buildList {
        if (dimensions.width > maximumAxis) {
            add(CaptureBudgetViolation(CaptureBudgetKind.WIDTH, dimensions.width.toLong(), maximumAxis.toLong()))
        }
        if (dimensions.height > maximumAxis) {
            add(CaptureBudgetViolation(CaptureBudgetKind.HEIGHT, dimensions.height.toLong(), maximumAxis.toLong()))
        }
        if (dimensions.length > maximumAxis) {
            add(CaptureBudgetViolation(CaptureBudgetKind.LENGTH, dimensions.length.toLong(), maximumAxis.toLong()))
        }
        if (dimensions.volume > maximumVolume) {
            add(CaptureBudgetViolation(CaptureBudgetKind.VOLUME, dimensions.volume, maximumVolume))
        }
    }

    public fun requireWithin(dimensions: Dimensions) {
        val violations = violations(dimensions)
        require(violations.isEmpty()) {
            violations.joinToString(prefix = "capture exceeds budget: ") {
                "${it.kind.name.lowercase()}=${it.actual} (limit ${it.limit})"
            }
        }
    }
}
