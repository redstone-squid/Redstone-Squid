package com.redstonesquid.minecraft.fabric

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals

class SubmissionRoutingTest {
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
}
