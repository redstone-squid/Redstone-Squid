package com.redstonesquid.minecraft.core.settings

import com.redstonesquid.minecraft.core.auth.ExpiredMinecraftGrantException
import com.redstonesquid.minecraft.core.auth.MinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.MissingMinecraftCredentialException
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.BackendRequest
import com.redstonesquid.minecraft.core.http.BackendTransport
import com.redstonesquid.minecraft.core.http.executeJson
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.time.Clock
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject

/** Reads and edits global policies using the linked player's account permissions. */
public class RuntimeSettingsClient(
    private val transport: BackendTransport,
    private val secretStore: MinecraftSecretStore,
    private val grantKey: PlayerGrantKey,
    private val paperInstallationKey: PaperInstallationKey?,
    private val clock: Clock = Clock.systemUTC(),
) {
    public fun request(name: String?, value: String?): CompletableFuture<JsonObject> {
        require(value == null || name != null) { "Choose a setting to change." }
        val patch = value?.let { JsonObject(mapOf(checkNotNull(name) to Json.parseToJsonElement(it))) }
        val grant = secretStore.loadPlayerGrant(grantKey)
            ?: throw MissingMinecraftCredentialException("player grant")
        if (!grant.isUsable(clock.instant())) throw ExpiredMinecraftGrantException()
        val headers = mutableMapOf("Authorization" to grant.authorizationHeader())
        if (grantKey.origin == MinecraftOrigin.PAPER) {
            val installation = secretStore.loadInstallation(checkNotNull(paperInstallationKey))
                ?: throw MissingMinecraftCredentialException("Paper installation")
            require(installation.installationId == grantKey.installationId) {
                "Paper installation does not match the player grant"
            }
            headers.putAll(installation.headers())
        }
        if (patch != null) headers["Idempotency-Key"] = UUID.randomUUID().toString()
        return transport.executeJson(
            BackendRequest(
                method = if (patch == null) BackendHttpMethod.GET else BackendHttpMethod.PATCH,
                pathAndQuery = "/settings/runtime",
                body = patch?.toString(),
                headers = headers,
                maxResponseBytes = 16 * 1024,
                requireNoStoreResponse = true,
            ),
            "runtime settings",
        ) { Json.parseToJsonElement(it).jsonObject }
    }
}
