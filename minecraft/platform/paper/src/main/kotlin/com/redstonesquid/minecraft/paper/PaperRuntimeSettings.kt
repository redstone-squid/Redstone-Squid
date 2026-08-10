package com.redstonesquid.minecraft.paper

import com.redstonesquid.minecraft.core.auth.PaperInstallationCredential
import com.redstonesquid.minecraft.core.http.MinecraftClientEndpoints
import java.util.UUID

internal class PaperRuntimeSettings private constructor(
    val endpoints: MinecraftClientEndpoints,
    private val installationId: UUID,
    private val installationSecret: String,
) {
    fun installationCredential(): PaperInstallationCredential =
        PaperInstallationCredential(installationId, installationSecret)

    override fun toString(): String =
        "PaperRuntimeSettings(endpoints=$endpoints, installationId=$installationId, installationSecret=<redacted>)"

    companion object {
        fun load(
            property: (String) -> String?,
            environment: (String) -> String?,
        ): PaperRuntimeSettings {
            val endpoints = MinecraftClientEndpoints.parse(
                setting(API_BASE_PROPERTY, API_BASE_ENV, property, environment),
                setting(APPROVAL_URI_PROPERTY, APPROVAL_URI_ENV, property, environment),
            )
            val installationId = UUID.fromString(
                requireNotNull(setting(INSTALLATION_ID_PROPERTY, INSTALLATION_ID_ENV, property, environment)) {
                    "Paper installation ID is not configured"
                },
            )
            val installationSecret = requireNotNull(environment(INSTALLATION_SECRET_ENV)?.takeIf(String::isNotBlank)) {
                "Paper installation secret is not configured"
            }
            return PaperRuntimeSettings(endpoints, installationId, installationSecret)
        }

        private fun setting(
            propertyName: String,
            environmentName: String,
            property: (String) -> String?,
            environment: (String) -> String?,
        ): String? = property(propertyName)?.trim()?.takeIf(String::isNotEmpty)
            ?: environment(environmentName)?.trim()?.takeIf(String::isNotEmpty)

        internal const val API_BASE_PROPERTY: String = "redstonesquid.apiBaseUri"
        internal const val API_BASE_ENV: String = "SQUID_MINECRAFT_API_BASE_URI"
        internal const val APPROVAL_URI_PROPERTY: String = "redstonesquid.approvalUri"
        internal const val APPROVAL_URI_ENV: String = "SQUID_MINECRAFT_APPROVAL_URI"
        internal const val INSTALLATION_ID_PROPERTY: String = "redstonesquid.installationId"
        internal const val INSTALLATION_ID_ENV: String = "SQUID_MINECRAFT_INSTALLATION_ID"
        internal const val INSTALLATION_SECRET_ENV: String = "SQUID_MINECRAFT_INSTALLATION_SECRET"
    }
}
