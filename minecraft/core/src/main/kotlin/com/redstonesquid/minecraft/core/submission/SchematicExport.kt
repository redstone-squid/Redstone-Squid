package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.protocol.MAX_SCHEMATIC_UPLOAD_BYTES
import java.io.ByteArrayOutputStream
import java.io.OutputStream

/** A named schematic whose detached data can be read or encoded off the game thread. */
public class SchematicExport(public val filename: String, private val write: (OutputStream) -> Unit) {
    internal fun read(): SchematicFile {
        val bytes = ByteArrayOutputStream()
        val output = object : OutputStream() {
            override fun write(value: Int) {
                requireSpace(1)
                bytes.write(value)
            }

            override fun write(data: ByteArray, offset: Int, length: Int) {
                requireSpace(length)
                bytes.write(data, offset, length)
            }

            private fun requireSpace(length: Int) {
                require(length <= MAX_SCHEMATIC_UPLOAD_BYTES - bytes.size()) {
                    "The schematic is larger than 16 MiB."
                }
            }
        }
        write(output)
        require(bytes.size() > 0) { "The schematic file is empty." }
        return SchematicFile(filename, bytes.toByteArray())
    }
}

internal class SchematicFile(val filename: String, val bytes: ByteArray)
