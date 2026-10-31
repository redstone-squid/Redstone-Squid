package com.redstonesquid.minecraft.core.auth

import com.redstonesquid.minecraft.core.http.RecordingBackendTransport
import com.redstonesquid.minecraft.core.http.jsonResponse
import com.redstonesquid.minecraft.protocol.MinecraftAuthApiPaths
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.security.MessageDigest
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class MinecraftDeviceAuthorizationTest {
    private val now = Instant.parse("2030-01-01T00:00:00Z")
    private val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174000")
    private val installationId = UUID.fromString("123e4567-e89b-12d3-a456-426614174010")

    @Test
    fun `Paper flow authenticates installation and persists a correctly bound grant`() {
        val store = InMemoryMinecraftSecretStore()
        val installationKey = PaperInstallationKey("paper:main")
        val installationSecret = "abcdefghijklmnopqrstuvwxyzABCDE_123456"
        store.saveInstallation(installationKey, PaperInstallationCredential(installationId, installationSecret))
        val transport = RecordingBackendTransport { request ->
            when (request.pathAndQuery) {
                MinecraftAuthApiPaths.PAPER_CHALLENGES -> jsonResponse(201, challengeJson())
                MinecraftAuthApiPaths.PAPER_EXCHANGE -> jsonResponse(body = grantJson("paper", installationId))
                else -> error("unexpected path")
            }
        }
        val client = client(transport, store)

        val pending = client.beginPaper(playerId, installationKey).join()
        val authorized = client.exchangePaper(pending).join()

        assertEquals(MinecraftOrigin.PAPER, authorized.key.origin)
        assertNotNull(store.loadPlayerGrant(authorized.key))
        assertEquals(
            listOf(MinecraftAuthApiPaths.PAPER_CHALLENGES, MinecraftAuthApiPaths.PAPER_EXCHANGE),
            transport.requests.map { it.pathAndQuery },
        )
        transport.requests.forEach { request ->
            assertEquals(installationId.toString(), request.headers["Squid-Installation-ID"])
            assertEquals(installationSecret, request.headers["Squid-Installation-Secret"])
            assertFalse("account" in checkNotNull(request.body))
            assertFalse("origin" in request.body)
            assertFalse(installationSecret in request.toString())
            assertTrue(request.requireNoStoreResponse)
        }
        assertEquals(
            "minecraft-exchange:123e4567-e89b-12d3-a456-426614174020",
            transport.requests.last().headers["Idempotency-Key"],
        )
    }

    @Test
    fun `Fabric proves its PKCE commitment and never submits caller authority`() {
        val store = InMemoryMinecraftSecretStore()
        var challenge: String? = null
        var exchangedVerifier: String? = null
        val transport = RecordingBackendTransport { request ->
            val body = Json.parseToJsonElement(checkNotNull(request.body)).jsonObject
            when (request.pathAndQuery) {
                MinecraftAuthApiPaths.FABRIC_CHALLENGES -> {
                    assertEquals(setOf("java_uuid", "pkce_s256_challenge"), body.keys)
                    challenge = body.getValue("pkce_s256_challenge").jsonPrimitive.content
                    jsonResponse(201, challengeJson())
                }
                MinecraftAuthApiPaths.FABRIC_EXCHANGE -> {
                    assertEquals(setOf("device_code", "pkce_verifier"), body.keys)
                    val verifier = body.getValue("pkce_verifier").jsonPrimitive.content
                    exchangedVerifier = verifier
                    val actual = Base64.getUrlEncoder().withoutPadding().encodeToString(
                        MessageDigest.getInstance("SHA-256").digest(verifier.encodeToByteArray()),
                    )
                    assertEquals(challenge, actual)
                    jsonResponse(body = grantJson("fabric", null))
                }
                else -> error("unexpected path")
            }
        }
        val client = client(transport, store)

        val pending = client.beginFabric(playerId).join()
        val authorized = client.exchangeFabric(pending).join()

        assertEquals(MinecraftOrigin.FABRIC, authorized.key.origin)
        assertEquals(null, authorized.key.installationId)
        assertNotNull(store.loadPlayerGrant(authorized.key))
        assertTrue("<redacted>" in pending.toString())
        assertFalse(checkNotNull(exchangedVerifier) in pending.toString())
    }

    @Test
    fun `client rejects a grant whose backend binding changed`() {
        val store = InMemoryMinecraftSecretStore()
        val transport = RecordingBackendTransport { request ->
            when (request.pathAndQuery) {
                MinecraftAuthApiPaths.FABRIC_CHALLENGES -> jsonResponse(201, challengeJson())
                else -> jsonResponse(body = grantJson("paper", installationId))
            }
        }
        val client = client(transport, store)
        val pending = client.beginFabric(playerId).join()

        val failure = assertFailsWith<java.util.concurrent.CompletionException> {
            client.exchangeFabric(pending).join()
        }

        assertTrue(failure.cause is IllegalStateException)
        assertEquals(null, store.loadPlayerGrant(PlayerGrantKey(playerId, MinecraftOrigin.FABRIC)))
    }

    @Test
    fun `default secret store fails closed`() {
        val client = MinecraftDeviceAuthorizationClient(
            RecordingBackendTransport { error("transport must not be reached") },
            clock = Clock.fixed(now, ZoneOffset.UTC),
        )

        assertFailsWith<SecretPersistenceUnavailableException> {
            client.beginPaper(playerId, PaperInstallationKey("paper:main"))
        }
    }

    private fun client(
        transport: RecordingBackendTransport,
        store: MinecraftSecretStore,
    ): MinecraftDeviceAuthorizationClient = MinecraftDeviceAuthorizationClient(
        transport = transport,
        secretStore = store,
        clock = Clock.fixed(now, ZoneOffset.UTC),
    )

    private fun challengeJson(): String =
        """
        {
          "id":"123e4567-e89b-12d3-a456-426614174020",
          "device_code":"abcdefghijklmnopqrstuvwxyzABCDE_123456",
          "user_code":"ABCD-EFGH-IJKL-MNOP",
          "expires_at":"2030-01-01T00:05:00Z",
          "polling_interval_seconds":5,
          "verification_uri":"https://www.example.test/link",
          "verification_uri_complete":"https://www.example.test/link?code=ABCD-EFGH-IJKL-MNOP"
        }
        """.trimIndent()

    private fun grantJson(origin: String, boundInstallationId: UUID?): String {
        val installation = boundInstallationId?.let { "\"$it\"" } ?: "null"
        return """
            {
              "grant_id":"123e4567-e89b-12d3-a456-426614174030",
              "token":"sqpt_123e4567e89b12d3a456426614174030_abcdefghijklmnopqrstuvwxyzABCDE_123456",
              "java_uuid":"$playerId",
              "origin":"$origin",
              "installation_id":$installation,
              "expires_at":"2030-01-01T00:10:00Z"
            }
        """.trimIndent()
    }
}
