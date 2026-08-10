package com.redstonesquid.minecraft.fabric.v26_1

import com.redstonesquid.minecraft.fabric.FabricGameAdapter
import com.redstonesquid.minecraft.snapshot.CaptureDisclosure
import com.redstonesquid.minecraft.snapshot.CuboidSelection
import com.redstonesquid.minecraft.snapshot.SafeSnapshot

/**
 * First explicit Minecraft-version boundary.
 *
 * The adapter stays fail-closed until block, block-entity, and entity extraction
 * have fidelity tests against a real 26.1.2 client. Returning `false` prevents
 * the common controller from invoking the unimplemented capture method.
 */
public object Minecraft261Adapter : FabricGameAdapter {
    override val minecraftVersion: String = "26.1.2"

    override fun isFullyLoaded(selection: CuboidSelection): Boolean = false

    override fun captureVisibleSelection(
        selection: CuboidSelection,
        disclosure: CaptureDisclosure,
    ): SafeSnapshot = throw UnsupportedOperationException(
        "Minecraft 26.1.2 world extraction is not connected; no partial schematic was produced",
    )
}
