package com.redstonesquid.minecraft.fabric

import com.redstonesquid.minecraft.snapshot.CaptureBudget
import com.redstonesquid.minecraft.snapshot.CaptureBudgetViolation
import com.redstonesquid.minecraft.snapshot.CuboidSelection
import com.redstonesquid.minecraft.snapshot.SchematicSnapshot

/**
 * Game-version boundary called synchronously on the client game thread.
 * Implementations read only loaded, client-visible state and return data detached from live game objects.
 */
public interface FabricGameAdapter {
    public val minecraftVersion: String

    public fun isFullyLoaded(selection: CuboidSelection): Boolean

    public fun captureVisibleSelection(selection: CuboidSelection): SchematicSnapshot
}

public sealed interface CapturePreparation {
    public data class Ready(public val snapshot: SchematicSnapshot) : CapturePreparation

    public data class OverBudget(public val violations: List<CaptureBudgetViolation>) : CapturePreparation

    public data object SelectionNotFullyLoaded : CapturePreparation
}

public class ClientCaptureController(
    private val adapter: FabricGameAdapter,
    private val budget: CaptureBudget = CaptureBudget(),
) {
    /** Checks the budget before world access and refuses an incompletely loaded selection. */
    public fun prepare(selection: CuboidSelection): CapturePreparation {
        val violations = budget.violations(selection.dimensions)
        if (violations.isNotEmpty()) {
            return CapturePreparation.OverBudget(violations)
        }
        if (!adapter.isFullyLoaded(selection)) {
            return CapturePreparation.SelectionNotFullyLoaded
        }
        return CapturePreparation.Ready(adapter.captureVisibleSelection(selection))
    }
}
