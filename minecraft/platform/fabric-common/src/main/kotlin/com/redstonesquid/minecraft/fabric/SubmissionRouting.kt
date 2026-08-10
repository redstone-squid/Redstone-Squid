package com.redstonesquid.minecraft.fabric

public enum class SubmissionRoute {
    STANDALONE,
    PAPER,
}

public data class PaperPresence(
    public val protocolVersion: Int,
    public val routingCapability: String,
)

public object SubmissionRouteDecider {
    public const val ROUTING_CAPABILITY: String = "redstonesquid.routing.v1"

    public fun decide(presence: PaperPresence?, supportedProtocol: IntRange): SubmissionRoute =
        if (
            presence != null &&
            presence.protocolVersion in supportedProtocol &&
            presence.routingCapability == ROUTING_CAPABILITY
        ) {
            SubmissionRoute.PAPER
        } else {
            SubmissionRoute.STANDALONE
        }
}
