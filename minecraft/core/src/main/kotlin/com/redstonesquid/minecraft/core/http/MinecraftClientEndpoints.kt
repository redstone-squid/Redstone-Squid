package com.redstonesquid.minecraft.core.http

import java.net.URI
import java.util.Locale

/**
 * Configured HTTPS endpoints for the Paper and Fabric entrypoints.
 * [parse] checks URI structure and the API's `/v1` suffix, without resolving or restricting host addresses.
 */
public data class MinecraftClientEndpoints(
    public val apiBaseUri: URI,
    public val approvalUri: URI,
) {
    public companion object {
        public fun parse(apiBaseUri: String?, approvalUri: String?): MinecraftClientEndpoints {
            require(!apiBaseUri.isNullOrBlank()) { "the Redstone Squid API base URI is not configured" }
            require(!approvalUri.isNullOrBlank()) { "the Redstone Squid approval URI is not configured" }
            return MinecraftClientEndpoints(
                apiBaseUri = requirePublicHttps(apiBaseUri, "API base URI", requireApiPrefix = true),
                approvalUri = requirePublicHttps(approvalUri, "approval URI", requireApiPrefix = false),
            )
        }

        private fun requirePublicHttps(value: String, name: String, requireApiPrefix: Boolean): URI {
            val parsed = runCatching { URI(value.trim()) }.getOrNull()
                ?: throw IllegalArgumentException("$name is not a valid URI")
            require(
                parsed.isAbsolute && parsed.scheme.lowercase(Locale.ROOT) == "https" && parsed.host != null &&
                    parsed.rawUserInfo == null && parsed.rawFragment == null,
            ) { "$name must be an absolute public HTTPS URI" }
            require(!requireApiPrefix || parsed.rawQuery == null) { "$name must not contain a query" }
            val normalizedPath = parsed.path.trimEnd('/') + "/"
            require(!requireApiPrefix || normalizedPath.endsWith("/v1/")) { "$name must end with /v1/" }
            return if (requireApiPrefix) {
                URI(parsed.scheme, null, parsed.host, parsed.port, normalizedPath, null, null)
            } else {
                parsed
            }
        }
    }
}
