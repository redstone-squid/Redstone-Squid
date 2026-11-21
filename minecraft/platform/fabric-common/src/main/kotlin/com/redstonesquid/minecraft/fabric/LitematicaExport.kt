package com.redstonesquid.minecraft.fabric

import com.redstonesquid.minecraft.core.submission.SchematicExport
import java.io.IOException
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path

/** Selects an explicit Litematica export from the Minecraft instance's schematics folder. */
public object LitematicaExport {
    public fun fromFile(schematicsDirectory: Path, filename: String): SchematicExport {
        require(filename.length in 1..255 && filename.endsWith(".litematic", ignoreCase = true) &&
            filename.none { it in "/\\:" || it.isISOControl() }
        ) {
            "Give the name of a .litematic file directly inside the instance's schematics folder."
        }
        val file = schematicsDirectory.resolve(filename)
        return SchematicExport(filename) { output ->
            require(Files.isDirectory(schematicsDirectory, NOFOLLOW_LINKS) && Files.isRegularFile(file, NOFOLLOW_LINKS)) {
                "That .litematic file was not found in the instance's schematics folder."
            }
            try {
                Files.newInputStream(file, NOFOLLOW_LINKS).use { it.transferTo(output) }
            } catch (_: IOException) {
                throw IllegalArgumentException("Could not read the selected .litematic file.")
            }
        }
    }
}
