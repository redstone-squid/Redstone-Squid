package com.redstonesquid.minecraft.fabric

public enum class SubmissionRoute {
    STANDALONE,
    PAPER,
}

/** Server presence whose authenticity must already have been established by the platform. */
public data class PaperPresence(
    public val protocolVersion: Int,
    public val routingCapability: String,
)

/**
 * Uses the Paper server's submission route when its advertised protocol and capability match.
 * Otherwise the Fabric client submits independently. The platform must authenticate server presence
 * before calling [decide]; this class only selects the route.
 */
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
