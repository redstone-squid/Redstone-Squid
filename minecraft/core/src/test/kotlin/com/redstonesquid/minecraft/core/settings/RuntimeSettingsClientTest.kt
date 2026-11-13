package com.redstonesquid.minecraft.core.settings

import com.redstonesquid.minecraft.core.auth.InMemoryMinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.PaperInstallationCredential
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.auth.PlayerGrantCredential
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.RecordingBackendTransport
import com.redstonesquid.minecraft.core.http.jsonResponse
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.junit.jupiter.api.Test

class RuntimeSettingsClientTest {
    @Test
    fun `Paper and Fabric settings edits use linked player credentials and preserve IDs`() {
        for (origin in MinecraftOrigin.entries) {
            val now = Instant.parse("2030-01-01T00:00:00Z")
            val playerId = UUID.randomUUID()
            val grantId = UUID.randomUUID()
            val installationId = UUID.randomUUID()
            val store = InMemoryMinecraftSecretStore()
            val installationKey = if (origin == MinecraftOrigin.PAPER) PaperInstallationKey("paper:test") else null
            if (installationKey != null) {
                store.saveInstallation(installationKey, PaperInstallationCredential(installationId, "abcdefghijklmnopqrstuvwxyzABCDE_123456"))
            }
            val key = PlayerGrantKey(playerId, origin, if (installationKey != null) installationId else null)
            store.savePlayerGrant(PlayerGrantCredential(
                grantId, key, "sqpt_${grantId.toString().replace("-", "")}_abcdefghijklmnopqrstuvwxyzABCDE_123456",
                now.plusSeconds(300),
            ))
            val transport = RecordingBackendTransport { jsonResponse(body = """{"welcome_channel_id":"1356094722531393681"}""") }
            val client = RuntimeSettingsClient(transport, store, key, installationKey, Clock.fixed(now, ZoneOffset.UTC))
            client.request("welcome_channel_id", "\"1356094722531393681\"").join()
            val request = transport.requests.single()
            assertEquals(BackendHttpMethod.PATCH, request.method)
            assertEquals("/settings/runtime", request.pathAndQuery)
            assertEquals("""{"welcome_channel_id":"1356094722531393681"}""", request.body)
            assertTrue(request.headers.getValue("Authorization").startsWith("Bearer sqpt_"))
            assertTrue(request.headers.containsKey("Idempotency-Key"))
            assertEquals(
                if (origin == MinecraftOrigin.PAPER) installationId.toString() else null,
                request.headers["Squid-Installation-ID"],
            )
            assertTrue(request.requireNoStoreResponse)
        }
    }
}
