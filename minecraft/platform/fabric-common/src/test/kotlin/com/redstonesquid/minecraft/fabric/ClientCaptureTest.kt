package com.redstonesquid.minecraft.fabric

import com.redstonesquid.minecraft.snapshot.BlockPosition
import com.redstonesquid.minecraft.snapshot.CaptureCompleteness
import com.redstonesquid.minecraft.snapshot.CaptureDisclosure
import com.redstonesquid.minecraft.snapshot.CuboidSelection
import com.redstonesquid.minecraft.snapshot.SafeBlockState
import com.redstonesquid.minecraft.snapshot.SafeSnapshot
import com.redstonesquid.minecraft.snapshot.SnapshotMetadata
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertIs

class ClientCaptureTest {
    @Test
    fun `controller refuses unloaded selections without reading world state`() {
        val adapter = FakeAdapter(loaded = false)
        val result = ClientCaptureController(adapter).prepare(
            CuboidSelection.fromCorners(BlockPosition(0, 0, 0), BlockPosition(0, 0, 0)),
            CaptureDisclosure(),
        )

        assertEquals(0, adapter.captureCalls)
        assertIs<CapturePreparation.SelectionNotFullyLoaded>(result)
    }

    @Test
    fun `routing requires an exact authenticated capability and compatible protocol`() {
        assertEquals(
            SubmissionRoute.PAPER,
            SubmissionRouteDecider.decide(PaperPresence(1, SubmissionRouteDecider.ROUTING_CAPABILITY), 1..2),
        )
        assertEquals(
            SubmissionRoute.STANDALONE,
            SubmissionRouteDecider.decide(PaperPresence(3, SubmissionRouteDecider.ROUTING_CAPABILITY), 1..2),
        )
        assertEquals(
            SubmissionRoute.STANDALONE,
            SubmissionRouteDecider.decide(PaperPresence(1, "untrusted"), 1..2),
        )
    }

    private class FakeAdapter(private val loaded: Boolean) : FabricGameAdapter {
        override val minecraftVersion: String = "test"
        var captureCalls: Int = 0

        override fun isFullyLoaded(selection: CuboidSelection): Boolean = loaded

        override fun captureVisibleSelection(
            selection: CuboidSelection,
            disclosure: CaptureDisclosure,
        ): SafeSnapshot {
            captureCalls += 1
            return SafeSnapshot(
                dataVersion = 1,
                dimensions = selection.dimensions,
                blocks = List(selection.dimensions.volume.toInt()) { SafeBlockState.AIR },
                metadata = SnapshotMetadata(
                    completeness = CaptureCompleteness.CLIENT_VISIBLE,
                    disclosure = disclosure,
                ),
            )
        }
    }
}
