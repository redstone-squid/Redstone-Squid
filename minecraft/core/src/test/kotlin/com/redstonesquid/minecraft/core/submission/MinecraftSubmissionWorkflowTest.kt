package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.core.auth.EphemeralMinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.PlayerGrantCredential
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.RecordingBackendTransport
import com.redstonesquid.minecraft.core.http.jsonResponse
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.SubmissionApiPaths
import java.net.URI
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID
import java.util.concurrent.Executor
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class MinecraftSubmissionWorkflowTest {
    private val now = Instant.parse("2030-01-01T00:00:00Z")

    @Test
    fun `failed authorization evicts idle sessions instead of exhausting the bound`() {
        val transport = RecordingBackendTransport {
            jsonResponse(503, """{"title":"Unavailable","status":503,"code":"service_unavailable"}""")
        }
        val workflow = workflow(transport, maxPlayerSessions = 1)
        val firstMessages = mutableListOf<String>()
        val secondMessages = mutableListOf<String>()

        workflow.link(UUID.fromString("123e4567-e89b-12d3-a456-426614174001"), firstMessages::add)
        assertEquals(0, workflow.activeSessionCount())
        workflow.link(UUID.fromString("123e4567-e89b-12d3-a456-426614174002"), secondMessages::add)

        assertEquals(0, workflow.activeSessionCount())
        assertFalse(secondMessages.any { "session limit" in it })
        workflow.close()
    }

    @Test
    fun `link message prefers backend complete verification URI`() {
        val transport = RecordingBackendTransport { jsonResponse(201, challengeJson()) }
        val workflow = workflow(transport)
        val messages = mutableListOf<String>()

        workflow.link(UUID.fromString("123e4567-e89b-12d3-a456-426614174001"), messages::add)

        assertTrue(messages.any { "https://www.example.test/link?code=ABCD-EFGH-IJKL-MNOP" in it })
        assertTrue(messages.any { "ABCD-EFGH-IJKL-MNOP" in it })
        workflow.close()
    }

    @Test
    fun `authorized player creates edits and cancels one synchronized draft`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = EphemeralMinecraftSecretStore(clock = Clock.fixed(now, ZoneOffset.UTC))
        val grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174030")
        store.savePlayerGrant(
            PlayerGrantCredential(
                grantId = grantId,
                key = PlayerGrantKey(playerId, MinecraftOrigin.FABRIC),
                token = "sqpt_${grantId.toString().replace("-", "")}_abcdefghijklmnopqrstuvwxyzABCDE_123456",
                expiresAt = now.plusSeconds(600),
            ),
        )
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> jsonResponse(201, draftJson(revision = 1))
                request.pathAndQuery.endsWith("/changes") -> jsonResponse(body = changeJson())
                request.method == BackendHttpMethod.DELETE -> jsonResponse(204, "")
                else -> error("unexpected request: $request")
            }
        }
        val workflow = MinecraftSubmissionWorkflow(
            origin = MinecraftOrigin.FABRIC,
            transport = transport,
            secretStore = store,
            approvalFallbackUri = URI("https://fallback.example.test/link"),
            callbackExecutor = Executor(Runnable::run),
            clock = Clock.fixed(now, ZoneOffset.UTC),
        )
        val messages = mutableListOf<String>()

        workflow.submit(playerId, "en-US", "door", messages::add)
        workflow.setField(playerId, "en-US", "description", "compact", messages::add)
        workflow.cancel(playerId, "en-US", messages::add)

        assertTrue(messages.any { it.startsWith("Created Door draft") })
        assertTrue(messages.any { "Saved 'description' at draft revision 2" in it })
        assertTrue(messages.any { it == "Synchronized draft deleted." })
        assertEquals(0, workflow.activeSessionCount())
        assertEquals(
            listOf(
                SubmissionApiPaths.CURRENT_FORM,
                SubmissionApiPaths.DRAFTS,
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040/changes",
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040",
            ),
            transport.requests.map { it.pathAndQuery },
        )
        assertTrue(checkNotNull(transport.requests.last().headers["Idempotency-Key"]).contains(":delete:"))
        workflow.close()
    }

    private fun workflow(
        transport: RecordingBackendTransport,
        maxPlayerSessions: Int = 8,
    ): MinecraftSubmissionWorkflow = MinecraftSubmissionWorkflow(
        origin = MinecraftOrigin.FABRIC,
        transport = transport,
        secretStore = EphemeralMinecraftSecretStore(),
        approvalFallbackUri = URI("https://fallback.example.test/link"),
        callbackExecutor = Executor(Runnable::run),
        clock = Clock.fixed(now, ZoneOffset.UTC),
        maxPlayerSessions = maxPlayerSessions,
    )

    private fun challengeJson(): String =
        """
        {
          "id":"123e4567-e89b-12d3-a456-426614174020",
          "device_code":"abcdefghijklmnopqrstuvwxyzABCDE_123456",
          "user_code":"ABCD-EFGH-IJKL-MNOP",
          "expires_at":"2030-01-01T00:05:00Z",
          "polling_interval_seconds":300,
          "verification_uri":"https://www.example.test/link",
          "verification_uri_complete":"https://www.example.test/link?code=ABCD-EFGH-IJKL-MNOP"
        }
        """.trimIndent()

    private fun manifestJson(): String =
        """
        {
          "schema_id":"build_submission.v1",
          "revision":1,
          "minimum_protocol":1,
          "maximum_protocol":1,
          "common_sections":[{
            "id":"identity",
            "title":"Identity",
            "fields":[{
              "id":"description",
              "label":"Description",
              "control":"text",
              "value_kind":"string",
              "origins":["fabric"]
            }]
          }],
          "categories":[{"code":"door","label":"Door","sections":[]}]
        }
        """.trimIndent()

    private fun draftJson(revision: Int): String =
        """
        {
          "id":"123e4567-e89b-12d3-a456-426614174040",
          "schema_id":"build_submission.v1",
          "schema_revision":1,
          "category":"door",
          "revision":$revision,
          "status":"editing",
          "answers":${if (revision == 1) "{}" else "{\"description\":\"compact\"}"},
          "origin":"fabric",
          "created_at":"2030-01-01T00:00:00Z",
          "updated_at":"2030-01-01T00:00:01Z",
          "expires_at":"2030-01-08T00:00:00Z"
        }
        """.trimIndent()

    private fun changeJson(): String = """{"draft":${draftJson(revision = 2)},"replayed":false}"""
}
