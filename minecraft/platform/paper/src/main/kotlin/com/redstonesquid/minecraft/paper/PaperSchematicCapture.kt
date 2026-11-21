package com.redstonesquid.minecraft.paper

import com.redstonesquid.minecraft.core.submission.SchematicExport
import com.redstonesquid.minecraft.snapshot.BlockPosition
import com.redstonesquid.minecraft.snapshot.CaptureBudget
import com.redstonesquid.minecraft.snapshot.CuboidSelection
import java.io.IOException
import java.util.UUID
import org.bukkit.Bukkit
import org.bukkit.Location
import org.bukkit.entity.Player
import org.bukkit.event.EventHandler
import org.bukkit.event.Listener
import org.bukkit.event.player.PlayerQuitEvent
import org.bukkit.util.BlockVector

/** Owns temporary Paper selections and takes snapshots on the server thread. */
internal class PaperSchematicCapture : Listener {
    private val selections = mutableMapOf<UUID, Selection>()
    private val budget = CaptureBudget(maximumVolume = 262_144)

    @EventHandler
    fun playerQuit(event: PlayerQuitEvent) {
        selections.remove(event.player.uniqueId)
    }

    fun mark(player: Player, first: Boolean, reset: Boolean = false): String {
        val worldId = player.world.uid
        val current = selections[player.uniqueId]
        if (!reset && current != null && current.worldId != worldId) {
            return "Both corners must be in the same world. Use /squid select to start over here."
        }
        val selection = if (current == null || reset) Selection(worldId) else current
        val location = player.location
        val position = BlockPosition(location.blockX, location.blockY, location.blockZ)
        if (first) selection.first = position else selection.second = position
        selections[player.uniqueId] = selection
        val number = if (first) 1 else 2
        val next = if (reset) " Move to the other corner and use /squid pos2." else ""
        return "Corner $number set at ${position.x}, ${position.y}, ${position.z}.$next"
    }

    fun describe(player: Player): String = try {
        val dimensions = region(player).dimensions
        val size = "Selection: ${dimensions.width} × ${dimensions.height} × ${dimensions.length} (${dimensions.volume} blocks)."
        val limits = budget.violations(dimensions)
        if (limits.isEmpty()) size else "$size ${budgetMessage()}"
    } catch (error: IllegalArgumentException) {
        error.message ?: "Invalid schematic selection."
    }

    fun capture(player: Player): SchematicExport {
        val region = region(player)
        val world = player.world
        val (min, max, size) = region
        require(budget.violations(size).isEmpty()) { budgetMessage() }
        require(min.y >= world.minHeight && max.y < world.maxHeight) { "Selection extends outside the world's build height." }
        var missing = 0
        for (x in (min.x shr 4)..(max.x shr 4)) {
            for (z in (min.z shr 4)..(max.z shr 4)) {
                if (!world.isChunkLoaded(x, z)) missing++
            }
        }
        require(missing == 0) { "$missing selected chunks are unloaded. Load them and try again." }

        val manager = Bukkit.getStructureManager()
        val structure = manager.createStructure()
        // Paper's corner overload omits the far corner. Pass the inclusive dimensions explicitly.
        structure.fill(
            Location(world, min.x.toDouble(), min.y.toDouble(), min.z.toDouble()),
            BlockVector(size.width, size.height, size.length),
            true,
        )
        return SchematicExport("selection-${min.x}-${min.y}-${min.z}.nbt") { output ->
            try {
                manager.saveStructure(output, structure)
            } catch (_: IOException) {
                throw IllegalArgumentException("Could not encode the Paper schematic.")
            }
        }
    }

    private fun region(player: Player): CuboidSelection {
        val selection = requireNotNull(selections[player.uniqueId]) { "Use /squid select to start a selection." }
        require(selection.worldId == player.world.uid) { "Return to the selection's world or use /squid select here." }
        val first = requireNotNull(selection.first) { "Set the first corner with /squid pos1." }
        val second = requireNotNull(selection.second) { "Set the second corner with /squid pos2." }
        return CuboidSelection.fromCorners(first, second)
    }

    private fun budgetMessage(): String =
        "Paper capture allows up to ${budget.maximumAxis} blocks per axis and ${budget.maximumVolume} blocks total."

    private class Selection(val worldId: UUID) {
        var first: BlockPosition? = null
        var second: BlockPosition? = null
    }
}
