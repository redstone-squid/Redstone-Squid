package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.core.auth.EphemeralMinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.PlayerGrantCredential
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.BackendTransportException
import com.redstonesquid.minecraft.core.http.RecordingBackendTransport
import com.redstonesquid.minecraft.core.http.jsonResponse
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.SubmissionApiPaths
import java.net.URI
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
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
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET ->
                    jsonResponse(body = """{"drafts":[]}""")
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
                SubmissionApiPaths.DRAFTS,
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040/changes",
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040",
            ),
            transport.requests.map { it.pathAndQuery },
        )
        assertTrue(checkNotNull(transport.requests.last().headers["Idempotency-Key"]).contains(":delete:"))
        workflow.close()
    }

    @Test
    fun `submit discovers and resumes the sole active draft after session loss`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = authorizedFabricStore(playerId)
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS ->
                    jsonResponse(body = draftListJson(draftSummaryJson()))
                request.pathAndQuery == "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040" ->
                    jsonResponse(body = draftJson(revision = 3))
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

        workflow.submit(playerId, "en-US", null, messages::add)

        assertTrue(messages.any { it.startsWith("Resumed Door draft") })
        assertTrue(messages.any { "revision 3" in it })
        assertEquals(
            listOf(
                SubmissionApiPaths.CURRENT_FORM,
                SubmissionApiPaths.DRAFTS,
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040",
            ),
            transport.requests.map { it.pathAndQuery },
        )
        assertFalse(transport.requests.any { it.method == BackendHttpMethod.POST })
        workflow.close()
    }

    @Test
    fun `category ambiguity refuses to guess and a full draft ID selects exactly one`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val selectedId = UUID.fromString("123e4567-e89b-12d3-a456-426614174041")
        val store = authorizedFabricStore(playerId)
        val summaries = draftListJson(
            draftSummaryJson(),
            draftSummaryJson(id = selectedId, displayName = "Second door", revision = 4),
        )
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> jsonResponse(body = summaries)
                request.pathAndQuery == "/submissions/drafts/$selectedId" ->
                    jsonResponse(body = draftJson(revision = 4, id = selectedId))
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

        assertTrue(messages.any { "More than one active 'Door' draft" in it })
        assertTrue(messages.any { selectedId.toString() in it })
        assertFalse(transport.requests.any { it.method == BackendHttpMethod.POST })

        messages.clear()
        workflow.submit(playerId, "en-US", selectedId.toString(), messages::add)

        assertTrue(messages.any { it.startsWith("Resumed Door draft $selectedId") })
        assertFalse(transport.requests.any { it.method == BackendHttpMethod.POST })
        workflow.close()
    }

    @Test
    fun `uncertain draft creation retries with the same idempotency key`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = authorizedFabricStore(playerId)
        val createAttempts = AtomicInteger()
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET ->
                    jsonResponse(body = draftListJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> when (createAttempts.incrementAndGet()) {
                    1, 2 -> throw BackendTransportException("response was lost")
                    3 -> jsonResponse(
                        409,
                        """{"title":"Pending","status":409,"code":"IDEMPOTENCY_IN_PROGRESS"}""",
                    )
                    else -> jsonResponse(201, draftJson(revision = 0))
                }
                else -> error("unexpected request: $request")
            }
        }
        val callbackExecutor = Executors.newSingleThreadExecutor { task -> Thread(task, "test-game-thread") }
        val workflow = MinecraftSubmissionWorkflow(
            origin = MinecraftOrigin.FABRIC,
            transport = transport,
            secretStore = store,
            approvalFallbackUri = URI("https://fallback.example.test/link"),
            callbackExecutor = callbackExecutor,
            clock = Clock.fixed(now, ZoneOffset.UTC),
        )
        val completed = CountDownLatch(1)
        val messages = mutableListOf<String>()
        val notificationThreads = mutableSetOf<String>()

        try {
            workflow.submit(playerId, "en-US", "door") { message ->
                messages += message
                notificationThreads += Thread.currentThread().name
                if (message.startsWith("Use /squid set")) completed.countDown()
            }

            assertTrue(completed.await(10, TimeUnit.SECONDS), messages.joinToString())
            val creates = transport.requests.filter {
                it.pathAndQuery == SubmissionApiPaths.DRAFTS && it.method == BackendHttpMethod.POST
            }
            assertEquals(4, creates.size)
            assertEquals(1, creates.map { it.body }.distinct().size)
            assertEquals(1, creates.map { it.headers["Idempotency-Key"] }.distinct().size)
            assertEquals(setOf("test-game-thread"), notificationThreads)
        } finally {
            workflow.close()
            callbackExecutor.shutdownNow()
        }
    }

    @Test
    fun `bounded retry exhaustion retains the create identity for manual reconciliation`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = authorizedFabricStore(playerId)
        val createAttempts = AtomicInteger()
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET ->
                    jsonResponse(body = draftListJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && createAttempts.incrementAndGet() <= 4 ->
                    throw BackendTransportException("response was lost")
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> jsonResponse(201, draftJson(revision = 0))
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
        val exhausted = CountDownLatch(1)
        val messages = mutableListOf<String>()
        val notify: (String) -> Unit = { message ->
            messages += message
            if (message.contains("could not be confirmed after bounded retries")) exhausted.countDown()
        }

        workflow.submit(playerId, "en-US", "door", notify)
        assertTrue(exhausted.await(10, TimeUnit.SECONDS), messages.joinToString())

        workflow.submit(playerId, "en-US", "door", notify)

        assertTrue(messages.any { it.startsWith("Created Door draft") })
        val creates = transport.requests.filter {
            it.pathAndQuery == SubmissionApiPaths.DRAFTS && it.method == BackendHttpMethod.POST
        }
        assertEquals(5, creates.size)
        assertEquals(1, creates.map { it.body }.distinct().size)
        assertEquals(1, creates.map { it.headers["Idempotency-Key"] }.distinct().size)
        workflow.close()
    }

    @Test
    fun `manual retry reconciles a committed uncertain creation before posting again`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = authorizedFabricStore(playerId)
        val listAttempts = AtomicInteger()
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET -> {
                    val body = if (listAttempts.incrementAndGet() == 1) {
                        draftListJson()
                    } else {
                        draftListJson(draftSummaryJson())
                    }
                    jsonResponse(body = body)
                }
                request.pathAndQuery == SubmissionApiPaths.DRAFTS ->
                    throw BackendTransportException("response was lost")
                request.pathAndQuery == "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040" ->
                    jsonResponse(body = draftJson(revision = 3))
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
        val exhausted = CountDownLatch(1)
        val messages = mutableListOf<String>()
        val notify: (String) -> Unit = { message ->
            messages += message
            if (message.contains("could not be confirmed after bounded retries")) exhausted.countDown()
        }

        workflow.submit(playerId, "en-US", "door", notify)
        assertTrue(exhausted.await(10, TimeUnit.SECONDS), messages.joinToString())

        workflow.submit(playerId, "en-US", "door", notify)

        assertTrue(messages.any { it.startsWith("Resumed Door draft") })
        val creates = transport.requests.filter {
            it.pathAndQuery == SubmissionApiPaths.DRAFTS && it.method == BackendHttpMethod.POST
        }
        assertEquals(4, creates.size)
        workflow.close()
    }

    @Test
    fun `resolved uncertain creation does not override an explicit different draft target`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val otherId = UUID.fromString("123e4567-e89b-12d3-a456-426614174041")
        val store = authorizedFabricStore(playerId)
        val listAttempts = AtomicInteger()
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET -> {
                    val body = if (listAttempts.incrementAndGet() == 1) {
                        draftListJson()
                    } else {
                        draftListJson(
                            draftSummaryJson(),
                            draftSummaryJson(id = otherId, category = "piston", displayName = "Piston workshop"),
                        )
                    }
                    jsonResponse(body = body)
                }
                request.pathAndQuery == SubmissionApiPaths.DRAFTS ->
                    throw BackendTransportException("response was lost")
                request.pathAndQuery == "/submissions/drafts/$otherId" ->
                    jsonResponse(body = draftJson(revision = 3, id = otherId, category = "piston"))
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
        val exhausted = CountDownLatch(1)
        val messages = mutableListOf<String>()
        val notify: (String) -> Unit = { message ->
            messages += message
            if (message.contains("could not be confirmed after bounded retries")) exhausted.countDown()
        }

        workflow.submit(playerId, "en-US", "door", notify)
        assertTrue(exhausted.await(10, TimeUnit.SECONDS), messages.joinToString())

        messages.clear()
        workflow.submit(playerId, "en-US", otherId.toString(), notify)

        assertTrue(messages.any { it.startsWith("Resumed Piston draft $otherId") })
        assertFalse(messages.any { it.startsWith("Resumed Door draft") })
        assertEquals(4, transport.requests.count { it.method == BackendHttpMethod.POST })
        workflow.close()
    }

    @Test
    fun `synchronous retry setup failure is reported on the game executor`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val grantKey = PlayerGrantKey(playerId, MinecraftOrigin.FABRIC)
        val store = authorizedFabricStore(playerId)
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS && request.method == BackendHttpMethod.GET ->
                    jsonResponse(body = draftListJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> {
                    store.removePlayerGrant(grantKey)
                    throw BackendTransportException("response was lost")
                }
                else -> error("unexpected request: $request")
            }
        }
        val callbackExecutor = Executors.newSingleThreadExecutor { task -> Thread(task, "test-game-thread") }
        val workflow = MinecraftSubmissionWorkflow(
            origin = MinecraftOrigin.FABRIC,
            transport = transport,
            secretStore = store,
            approvalFallbackUri = URI("https://fallback.example.test/link"),
            callbackExecutor = callbackExecutor,
            clock = Clock.fixed(now, ZoneOffset.UTC),
        )
        val reported = CountDownLatch(1)
        val notificationThreads = mutableSetOf<String>()

        try {
            workflow.submit(playerId, "en-US", "door") { message ->
                notificationThreads += Thread.currentThread().name
                if (message.contains("authorization is unavailable or expired")) reported.countDown()
            }

            assertTrue(reported.await(5, TimeUnit.SECONDS))
            assertEquals(setOf("test-game-thread"), notificationThreads)
            assertEquals(1, transport.requests.count { it.method == BackendHttpMethod.POST })
        } finally {
            workflow.close()
            callbackExecutor.shutdownNow()
        }
    }

    @Test
    fun `remote revision change invalidates a resumed draft while listing multiple drafts`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val firstId = UUID.fromString("123e4567-e89b-12d3-a456-426614174040")
        val secondId = UUID.fromString("123e4567-e89b-12d3-a456-426614174041")
        val store = authorizedFabricStore(playerId)
        var remoteRevision = 3
        val transport = RecordingBackendTransport { request ->
            when {
                request.pathAndQuery == SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                request.pathAndQuery == SubmissionApiPaths.DRAFTS -> jsonResponse(
                    body = draftListJson(
                        draftSummaryJson(id = firstId, revision = remoteRevision),
                        draftSummaryJson(id = secondId, displayName = "Second door"),
                    ),
                )
                request.pathAndQuery == "/submissions/drafts/$firstId" ->
                    jsonResponse(body = draftJson(revision = 3, id = firstId))
                else -> error("stale draft must not be edited: $request")
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

        workflow.submit(playerId, "en-US", firstId.toString(), messages::add)
        assertTrue(messages.any { it.startsWith("Resumed Door draft") })

        remoteRevision = 4
        messages.clear()
        workflow.submit(playerId, "en-US", null, messages::add)
        workflow.status(playerId, messages::add)
        workflow.setField(playerId, "en-US", "description", "stale edit", messages::add)

        assertTrue(messages.any { it == "No synchronized draft is active on this client." })
        assertTrue(messages.any { it == "No synchronized draft is active. Use /squid submit <category> first." })
        assertFalse(transport.requests.any { it.pathAndQuery.endsWith("/changes") })
        workflow.close()
    }

    @Test
    fun `remote processing transition invalidates a previously resumed editing session`() {
        val playerId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val store = authorizedFabricStore(playerId)
        var processing = false
        val transport = RecordingBackendTransport { request ->
            when (request.pathAndQuery) {
                SubmissionApiPaths.CURRENT_FORM -> jsonResponse(body = manifestJson())
                SubmissionApiPaths.DRAFTS ->
                    jsonResponse(
                        body = draftListJson(
                            draftSummaryJson(status = if (processing) "processing" else "editing"),
                        ),
                    )
                "/submissions/drafts/123e4567-e89b-12d3-a456-426614174040" ->
                    jsonResponse(body = draftJson(revision = 3))
                else -> error("processing draft must not be fetched for editing: $request")
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

        workflow.submit(playerId, "en-US", null, messages::add)
        assertTrue(messages.any { it.startsWith("Resumed Door draft") })

        processing = true
        messages.clear()
        workflow.submit(playerId, "en-US", "door", messages::add)

        assertTrue(messages.any { "still processing and cannot be edited" in it })
        workflow.status(playerId, messages::add)
        assertTrue(messages.any { it == "No synchronized draft is active on this client." })
        workflow.setField(playerId, "en-US", "description", "stale edit", messages::add)
        assertTrue(messages.any { it == "No synchronized draft is active. Use /squid submit <category> first." })
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

    private fun authorizedFabricStore(playerId: UUID): EphemeralMinecraftSecretStore =
        EphemeralMinecraftSecretStore(clock = Clock.fixed(now, ZoneOffset.UTC)).also { store ->
            val grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174030")
            store.savePlayerGrant(
                PlayerGrantCredential(
                    grantId = grantId,
                    key = PlayerGrantKey(playerId, MinecraftOrigin.FABRIC),
                    token = "sqpt_${grantId.toString().replace("-", "")}_abcdefghijklmnopqrstuvwxyzABCDE_123456",
                    expiresAt = now.plusSeconds(600),
                ),
            )
        }

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
          "categories":[
            {"code":"door","label":"Door","sections":[]},
            {"code":"piston","label":"Piston","sections":[]}
          ]
        }
        """.trimIndent()

    private fun draftJson(
        revision: Int,
        id: UUID = UUID.fromString("123e4567-e89b-12d3-a456-426614174040"),
        category: String = "door",
    ): String =
        """
        {
          "id":"$id",
          "schema_id":"build_submission.v1",
          "schema_revision":1,
          "category":"$category",
          "revision":$revision,
          "status":"editing",
          "answers":${if (revision == 1) "{}" else "{\"description\":\"compact\"}"},
          "origin":"fabric",
          "created_at":"2030-01-01T00:00:00Z",
          "updated_at":"2030-01-01T00:00:01Z",
          "expires_at":"2030-01-08T00:00:00Z"
        }
        """.trimIndent()

    private fun draftSummaryJson(
        id: UUID = UUID.fromString("123e4567-e89b-12d3-a456-426614174040"),
        displayName: String = "Workshop door",
        status: String = "editing",
        revision: Int = 3,
        category: String = "door",
    ): String =
        """
        {
          "id":"$id",
          "schema_id":"build_submission.v1",
          "schema_revision":1,
          "category":"$category",
          "revision":$revision,
          "status":"$status",
          "origin":"fabric",
          "display_name":"$displayName",
          "created_at":"2030-01-01T00:00:00Z",
          "updated_at":"2030-01-01T00:00:01Z",
          "expires_at":"2030-01-08T00:00:00Z"
        }
        """.trimIndent()

    private fun draftListJson(vararg summaries: String): String =
        """{"drafts":[${summaries.joinToString(",")}]}"""

    private fun changeJson(): String = """{"draft":${draftJson(revision = 2)},"replayed":false}"""
}
