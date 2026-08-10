package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.core.auth.ExpiredMinecraftGrantException
import com.redstonesquid.minecraft.core.auth.InMemoryMinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.PaperInstallationCredential
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.auth.PlayerGrantCredential
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.RecordingBackendTransport
import com.redstonesquid.minecraft.core.http.jsonResponse
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.SubmissionApiPaths
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SubmissionDraftClientTest {
    private val now = Instant.parse("2030-01-01T00:00:00Z")
    private val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174000")
    private val installationId = UUID.fromString("123e4567-e89b-12d3-a456-426614174010")
    private val draftId = UUID.fromString("123e4567-e89b-12d3-a456-426614174020")

    @Test
    fun `Paper draft creation derives origin and authenticates both credentials`() {
        val (store, grantKey, installationKey) = paperCredentials()
        val transport = RecordingBackendTransport { jsonResponse(201, draftJson(origin = "paper")) }
        val client = client(transport, store, grantKey, installationKey)

        val draft = client.createDraft("door", setOf("schematic_capture")).join()

        assertEquals("paper", draft.origin)
        val request = transport.requests.single()
        val body = Json.parseToJsonElement(checkNotNull(request.body)).jsonObject
        assertEquals(setOf("category", "origin", "client_capabilities"), body.keys)
        assertEquals("paper", body.getValue("origin").jsonPrimitive.content)
        assertFalse("account_id" in body)
        assertTrue(checkNotNull(request.headers["Authorization"]).startsWith("Bearer sqpt_"))
        assertEquals(installationId.toString(), request.headers["X-Squid-Installation-ID"])
        assertEquals("no-store", request.headers["Cache-Control"])
    }

    @Test
    fun `prepared optimistic change is byte-identical across retries`() {
        val (store, grantKey, installationKey) = paperCredentials()
        val transport = RecordingBackendTransport { jsonResponse(body = changeJson()) }
        val client = client(transport, store, grantKey, installationKey)
        val draft = FormDraftFixtures.storedDraft(draftJson(origin = "paper"))
        val pending = client.prepareChange(
            draft,
            listOf(
                DraftFieldMutation.Set("description", JsonPrimitive("hello")),
                DraftFieldMutation.Unset("display_name"),
            ),
        )

        val first = client.submitChange(pending).join()
        val second = client.submitChange(pending).join()

        assertEquals(2, first.draft.revision)
        assertTrue(second.replayed)
        assertEquals(transport.requests[0].body, transport.requests[1].body)
        val body = Json.parseToJsonElement(checkNotNull(transport.requests[0].body)).jsonObject
        assertEquals(1, body.getValue("base_revision").jsonPrimitive.content.toInt())
        assertTrue(body.getValue("idempotency_key").jsonPrimitive.content.startsWith("paper:test:"))
        assertTrue("<redacted>" in pending.toString())
        assertFalse("hello" in pending.toString())
    }

    @Test
    fun `public form read does not touch the secret store`() {
        val transport = RecordingBackendTransport { jsonResponse(body = manifestJson()) }
        val client = SubmissionDraftClient(
            transport = transport,
            secretStore = com.redstonesquid.minecraft.core.auth.FailClosedMinecraftSecretStore,
            grantKey = PlayerGrantKey(playerId, MinecraftOrigin.FABRIC),
            clientInstanceId = "fabric:test",
            locale = "en-US",
            clock = Clock.fixed(now, ZoneOffset.UTC),
        )

        val manifest = client.currentForm().join()

        assertEquals("redstone_squid_submission", manifest.schemaId)
        assertEquals(mapOf("Accept-Language" to "en-US"), transport.requests.single().headers)
    }

    @Test
    fun `expired player grant fails before transport`() {
        val store = InMemoryMinecraftSecretStore()
        val key = PlayerGrantKey(playerId, MinecraftOrigin.FABRIC)
        store.savePlayerGrant(
            PlayerGrantCredential(
                grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174030"),
                key = key,
                token = playerToken(),
                expiresAt = now,
            ),
        )
        val transport = RecordingBackendTransport { error("transport must not be reached") }
        val client = client(transport, store, key, null)

        assertFailsWith<ExpiredMinecraftGrantException> { client.getDraft(draftId) }
        assertTrue(transport.requests.isEmpty())
    }

    private fun paperCredentials(): Triple<InMemoryMinecraftSecretStore, PlayerGrantKey, PaperInstallationKey> {
        val store = InMemoryMinecraftSecretStore()
        val installationKey = PaperInstallationKey("paper:main")
        store.saveInstallation(
            installationKey,
            PaperInstallationCredential(installationId, "abcdefghijklmnopqrstuvwxyzABCDE_123456"),
        )
        val grantKey = PlayerGrantKey(playerId, MinecraftOrigin.PAPER, installationId)
        store.savePlayerGrant(
            PlayerGrantCredential(
                grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174030"),
                key = grantKey,
                token = playerToken(),
                expiresAt = now.plusSeconds(300),
            ),
        )
        return Triple(store, grantKey, installationKey)
    }

    private fun client(
        transport: RecordingBackendTransport,
        store: InMemoryMinecraftSecretStore,
        grantKey: PlayerGrantKey,
        installationKey: PaperInstallationKey?,
    ): SubmissionDraftClient = SubmissionDraftClient(
        transport = transport,
        secretStore = store,
        grantKey = grantKey,
        clientInstanceId = if (grantKey.origin == MinecraftOrigin.PAPER) "paper:test" else "fabric:test",
        locale = "en-US",
        paperInstallationKey = installationKey,
        clock = Clock.fixed(now, ZoneOffset.UTC),
    )

    private fun playerToken(): String =
        "sqpt_123e4567e89b12d3a456426614174030_abcdefghijklmnopqrstuvwxyzABCDE_123456"

    private fun draftJson(origin: String, revision: Int = 1): String =
        """
        {
          "id":"$draftId",
          "schema_id":"redstone_squid_submission",
          "schema_revision":1,
          "category":"door",
          "revision":$revision,
          "status":"editing",
          "answers":{},
          "origin":"$origin",
          "created_at":"2030-01-01T00:00:00Z",
          "updated_at":"2030-01-01T00:00:01Z",
          "expires_at":"2030-01-08T00:00:00Z"
        }
        """.trimIndent()

    private fun changeJson(): String =
        """{"draft":${draftJson(origin = "paper", revision = 2)},"replayed":true}"""

    private fun manifestJson(): String =
        """
        {
          "schema_id":"redstone_squid_submission",
          "revision":1,
          "minimum_protocol":1,
          "maximum_protocol":1,
          "common_sections":[],
          "categories":[{"code":"door","label":"Door","sections":[]}]
        }
        """.trimIndent()
}

private object FormDraftFixtures {
    fun storedDraft(document: String): com.redstonesquid.minecraft.protocol.StoredDraft =
        com.redstonesquid.minecraft.protocol.FormProtocolJson.decodeStoredDraft(document)
}
