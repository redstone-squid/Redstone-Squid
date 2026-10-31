package com.redstonesquid.minecraft.snapshot

public enum class NbtType(public val id: Int) {
    END(0),
    BYTE(1),
    SHORT(2),
    INT(3),
    LONG(4),
    FLOAT(5),
    DOUBLE(6),
    BYTE_ARRAY(7),
    STRING(8),
    LIST(9),
    COMPOUND(10),
    INT_ARRAY(11),
    LONG_ARRAY(12),
}

public sealed interface NbtValue {
    public val type: NbtType
}

@JvmInline
public value class NbtByte(public val value: Byte) : NbtValue {
    override val type: NbtType
        get() = NbtType.BYTE
}

@JvmInline
public value class NbtShort(public val value: Short) : NbtValue {
    override val type: NbtType
        get() = NbtType.SHORT
}

@JvmInline
public value class NbtInt(public val value: Int) : NbtValue {
    override val type: NbtType
        get() = NbtType.INT
}

@JvmInline
public value class NbtLong(public val value: Long) : NbtValue {
    override val type: NbtType
        get() = NbtType.LONG
}

@JvmInline
public value class NbtFloat(public val value: Float) : NbtValue {
    override val type: NbtType
        get() = NbtType.FLOAT
}

@JvmInline
public value class NbtDouble(public val value: Double) : NbtValue {
    override val type: NbtType
        get() = NbtType.DOUBLE
}

public class NbtByteArray(value: ByteArray) : NbtValue {
    public val value: ByteArray = value.copyOf()

    override val type: NbtType
        get() = NbtType.BYTE_ARRAY

    override fun equals(other: Any?): Boolean = other is NbtByteArray && value.contentEquals(other.value)

    override fun hashCode(): Int = value.contentHashCode()
}

@JvmInline
public value class NbtString(public val value: String) : NbtValue {
    override val type: NbtType
        get() = NbtType.STRING
}

public data class NbtList private constructor(
    public val elementType: NbtType,
    public val values: List<NbtValue>,
) : NbtValue {
    override val type: NbtType
        get() = NbtType.LIST

    public companion object {
        public fun of(values: List<NbtValue>): NbtList {
            val copiedValues = values.toList()
            val elementType = copiedValues.firstOrNull()?.type ?: NbtType.END
            require(copiedValues.all { it.type == elementType }) { "NBT list elements must have one type" }
            return NbtList(elementType, copiedValues)
        }
    }
}

public data class NbtCompound private constructor(public val values: Map<String, NbtValue>) : NbtValue {
    override val type: NbtType
        get() = NbtType.COMPOUND

    public val isEmpty: Boolean
        get() = values.isEmpty()

    public companion object {
        public fun of(values: Map<String, NbtValue> = emptyMap()): NbtCompound {
            require(values.keys.none(String::isEmpty)) { "NBT compound keys must not be empty" }
            return NbtCompound(values.toSortedMap())
        }

        public fun of(vararg values: Pair<String, NbtValue>): NbtCompound = of(mapOf(*values))
    }
}

public class NbtIntArray(value: IntArray) : NbtValue {
    public val value: IntArray = value.copyOf()

    override val type: NbtType
        get() = NbtType.INT_ARRAY

    override fun equals(other: Any?): Boolean = other is NbtIntArray && value.contentEquals(other.value)

    override fun hashCode(): Int = value.contentHashCode()
}

public class NbtLongArray(value: LongArray) : NbtValue {
    public val value: LongArray = value.copyOf()

    override val type: NbtType
        get() = NbtType.LONG_ARRAY

    override fun equals(other: Any?): Boolean = other is NbtLongArray && value.contentEquals(other.value)

    override fun hashCode(): Int = value.contentHashCode()
}
