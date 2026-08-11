package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.core.auth.AuthorizedPlayer
import com.redstonesquid.minecraft.core.auth.ExpiredMinecraftGrantException
import com.redstonesquid.minecraft.core.auth.MinecraftDeviceAuthorizationClient
import com.redstonesquid.minecraft.core.auth.MinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.MissingMinecraftCredentialException
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.auth.PendingFabricAuthorization
import com.redstonesquid.minecraft.core.auth.PendingPaperAuthorization
import com.redstonesquid.minecraft.core.auth.PendingPlayerAuthorization
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendApiException
import com.redstonesquid.minecraft.core.http.BackendTransport
import com.redstonesquid.minecraft.core.http.BackendTransportException
import com.redstonesquid.minecraft.protocol.ClientCapabilities
import com.redstonesquid.minecraft.protocol.CURRENT_PROTOCOL_VERSION
import com.redstonesquid.minecraft.protocol.DraftListResponse
import com.redstonesquid.minecraft.protocol.DraftSummary
import com.redstonesquid.minecraft.protocol.FormCapabilityNegotiator
import com.redstonesquid.minecraft.protocol.FormField
import com.redstonesquid.minecraft.protocol.FormManifest
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.StoredDraft
import java.net.URI
import java.time.Clock
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionException
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement

/**
 * Bounded, ephemeral in-game authorization and synchronized-draft session.
 * It deliberately has no media, schematic, capture, or final-submission operation.
 */
public class MinecraftSubmissionWorkflow(
    private val origin: MinecraftOrigin,
    private val transport: BackendTransport,
    private val secretStore: MinecraftSecretStore,
    private val approvalFallbackUri: URI,
    private val callbackExecutor: Executor,
    private val paperInstallationKey: PaperInstallationKey? = null,
    private val clock: Clock = Clock.systemUTC(),
    private val scheduler: ScheduledExecutorService = daemonScheduler(),
    private val maxPlayerSessions: Int = 1_024,
) : AutoCloseable {
    private val authorization = MinecraftDeviceAuthorizationClient(transport, secretStore, clock)
    private val sessions = LinkedHashMap<UUID, PlayerSession>()
    private val instanceId = UUID.randomUUID().toString()

    init {
        require(approvalFallbackUri.scheme.equals("https", ignoreCase = true) && approvalFallbackUri.host != null) {
            "approval fallback URI must use HTTPS"
        }
        require((origin == MinecraftOrigin.PAPER) == (paperInstallationKey != null)) {
            "Paper workflows require an installation key and Fabric workflows must not have one"
        }
        require(maxPlayerSessions in 1..100_000) { "max player sessions is outside the supported range" }
    }

    public fun link(playerId: UUID, notify: (String) -> Unit) {
        val session = session(playerId, notify) ?: return
        synchronized(session) {
            if (session.linking || session.pendingAuthorization != null) {
                notify("Redstone Squid linking is already waiting for approval. Use /squid status for details.")
                return
            }
            session.linking = true
        }
        val future = runCatching {
            when (origin) {
                MinecraftOrigin.PAPER -> authorization.beginPaper(playerId, checkNotNull(paperInstallationKey))
                MinecraftOrigin.FABRIC -> authorization.beginFabric(playerId)
            }
        }.getOrElse { error ->
            synchronized(session) { session.linking = false }
            removeIfIdle(playerId, session)
            notify(safeFailure(error))
            return
        }
        future.whenComplete { pending, failure ->
            dispatch {
                if (failure != null) {
                    synchronized(session) { session.linking = false }
                    removeIfIdle(playerId, session)
                    notify(safeFailure(failure))
                    return@dispatch
                }
                val authorization = checkNotNull(pending)
                synchronized(session) {
                    session.linking = false
                    session.pendingAuthorization = authorization
                }
                val verification = authorization.verificationUriComplete
                    ?: authorization.verificationUri
                    ?: approvalFallbackUri.toString()
                notify("Authorize Redstone Squid at $verification")
                notify("Enter code ${authorization.userCode}. It expires at ${authorization.expiresAt}.")
                schedulePoll(playerId, session, authorization, notify, authorization.pollingIntervalSeconds.toLong())
            }
        }
    }

    public fun submit(playerId: UUID, locale: String, target: String?, notify: (String) -> Unit) {
        val session = session(playerId, notify) ?: return
        if (!requireGrant(playerId, notify)) {
            removeIfIdle(playerId, session)
            return
        }
        synchronized(session) {
            if (session.busy) {
                notify("A Redstone Squid draft operation is already in progress.")
                return
            }
            session.busy = true
        }
        val client = draftClient(playerId, locale)
        val discovery = runCatching {
            client.currentForm().thenCombine(client.listDrafts()) { manifest, drafts -> manifest to drafts }
        }.getOrElse { error ->
            synchronized(session) { session.busy = false }
            removeIfIdle(playerId, session)
            notify(safeFailure(error))
            return
        }
        discovery.whenComplete { result, failure ->
            dispatch {
                if (failure != null) {
                    synchronized(session) { session.busy = false }
                    removeIfIdle(playerId, session)
                    notify(safeFailure(failure))
                    return@dispatch
                }
                val (manifest, drafts) = checkNotNull(result)
                continueSubmit(playerId, session, client, manifest, drafts, target, notify)
            }
        }
    }

    public fun status(playerId: UUID, notify: (String) -> Unit) {
        val session = synchronized(sessions) { sessions[playerId] }
        val key = runCatching { grantKey(playerId) }.getOrNull()
        val grant = key?.let(secretStore::loadPlayerGrant)
        if (grant?.isUsable(clock.instant()) == true) {
            notify("Redstone Squid account linked until ${grant.expiresAt}.")
        } else {
            notify("Redstone Squid account is not linked. Use /squid link.")
        }
        if (session == null) {
            notify("No synchronized draft is active on this client.")
            return
        }
        synchronized(session) {
            session.pendingAuthorization?.let {
                notify("Authorization code ${it.userCode} is waiting for approval until ${it.expiresAt}.")
            }
            session.pendingCreate?.let {
                notify("Draft creation for '${it.category}' is awaiting confirmation; /squid submit will retry it.")
            }
            val draft = session.draft
            if (draft == null) {
                notify("No synchronized draft is active on this client.")
            } else {
                val missing = session.manifest?.let { missingRequiredFields(it, draft) }.orEmpty()
                val suffix = if (missing.isEmpty()) {
                    "No required field is visibly missing."
                } else {
                    "Missing required fields: ${missing.take(8).joinToString()}${if (missing.size > 8) ", …" else ""}."
                }
                notify(
                    "Draft ${draft.id}: ${draft.category}, revision ${draft.revision}, " +
                        "${draft.answers.size} answers. $suffix",
                )
            }
        }
    }

    public fun cancel(playerId: UUID, locale: String, notify: (String) -> Unit) {
        val session = synchronized(sessions) { sessions[playerId] }
        if (session == null) {
            notify("No synchronized draft is active on this client.")
            return
        }
        val draft = synchronized(session) {
            if (session.busy) {
                notify("A Redstone Squid draft operation is already in progress.")
                return
            }
            val current = session.draft
            if (current == null) {
                notify("No synchronized draft is active on this client.")
                return
            }
            session.busy = true
            current
        }
        draftClient(playerId, locale).deleteDraft(UUID.fromString(draft.id)).whenComplete { _, failure ->
            dispatch {
                synchronized(session) {
                    session.busy = false
                    if (failure == null && session.draft?.id == draft.id) {
                        session.draft = null
                        session.manifest = null
                    }
                }
                if (failure == null) {
                    removeIfIdle(playerId, session)
                }
                notify(if (failure == null) "Synchronized draft deleted." else safeFailure(failure))
            }
        }
    }

    public fun setField(
        playerId: UUID,
        locale: String,
        fieldId: String,
        rawValue: String,
        notify: (String) -> Unit,
    ) {
        mutateField(playerId, locale, fieldId, rawValue, unset = false, notify)
    }

    public fun unsetField(playerId: UUID, locale: String, fieldId: String, notify: (String) -> Unit) {
        mutateField(playerId, locale, fieldId, null, unset = true, notify)
    }

    override fun close() {
        scheduler.shutdownNow()
        synchronized(sessions) { sessions.clear() }
    }

    private fun continueSubmit(
        playerId: UUID,
        session: PlayerSession,
        client: SubmissionDraftClient,
        manifest: FormManifest,
        discovery: DraftListResponse,
        target: String?,
        notify: (String) -> Unit,
    ) {
        val summaries = discovery.drafts
        synchronized(session) {
            val current = session.draft
            val currentSummary = current?.let { draft ->
                val draftId = UUID.fromString(draft.id)
                summaries.singleOrNull { UUID.fromString(it.id) == draftId }
            }
            if (current != null && (currentSummary == null || !matchesCurrent(manifest, current, currentSummary))) {
                session.draft = null
                session.manifest = null
            }
        }
        val unresolvedCreate = synchronized(session) { session.pendingCreate }
        if (unresolvedCreate != null) {
            val matching = summaries.filter { it.category == unresolvedCreate.category }
            if (matching.size > 1) {
                clearPendingCreate(session, unresolvedCreate)
                finishSubmit(playerId, session)
                notify(
                    "Draft creation for '${unresolvedCreate.category}' completed ambiguously; " +
                        "choose the intended draft by its full ID.",
                )
                showDrafts(matching, notify)
                notify("Use /squid submit <draft-id> to resume exactly one draft.")
                return
            }
            if (matching.size == 1) {
                clearPendingCreate(session, unresolvedCreate)
                val resolved = matching.single()
                val requestedId = target?.let(::canonicalUuid)
                if (
                    target == null ||
                    target == unresolvedCreate.category ||
                    requestedId == UUID.fromString(resolved.id)
                ) {
                    resumeDraft(playerId, session, client, manifest, resolved, notify)
                    return
                }
            } else if (target == null || target == unresolvedCreate.category) {
                if (!client.canSafelyReplay(unresolvedCreate)) {
                    clearPendingCreate(session, unresolvedCreate)
                    finishSubmit(playerId, session)
                    notify(
                        "The earlier draft creation was not found and its authenticated replay window changed. " +
                            "Run /squid submit ${unresolvedCreate.category} again to start a new request.",
                    )
                    return
                }
                val label = manifest.categories.singleOrNull { it.code == unresolvedCreate.category }?.label
                    ?: unresolvedCreate.category
                submitPreparedCreate(
                    playerId,
                    session,
                    client,
                    manifest,
                    label,
                    unresolvedCreate,
                    notify,
                    attempt = 1,
                )
                return
            } else {
                finishSubmit(playerId, session)
                notify(
                    "Draft creation for '${unresolvedCreate.category}' is still unresolved. " +
                        "Use /squid submit ${unresolvedCreate.category} to reconcile it first.",
                )
                return
            }
        }
        if (target == null) {
            if (summaries.size == 1 && canEdit(manifest, summaries.single())) {
                resumeDraft(playerId, session, client, manifest, summaries.single(), notify)
                return
            }
            finishDiscovery(playerId, session, manifest, summaries, notify)
            return
        }
        val requestedId = canonicalUuid(target)
        if (requestedId != null) {
            val selected = summaries.singleOrNull { UUID.fromString(it.id) == requestedId }
            if (selected == null) {
                finishSubmit(playerId, session)
                notify("No active synchronized draft matches '$target'. Run /squid submit to refresh the list.")
                return
            }
            resumeDraft(playerId, session, client, manifest, selected, notify)
            return
        }
        val categoryForm = manifest.categories.singleOrNull { it.code == target }
        if (categoryForm == null) {
            finishSubmit(playerId, session)
            notify(
                "Unknown build category or draft ID '$target'. " +
                    "Use /squid submit to list active drafts and categories.",
            )
            return
        }
        val matching = summaries.filter { it.category == target }
        if (matching.size > 1) {
            finishSubmit(playerId, session)
            notify("More than one active '${categoryForm.label}' draft exists; choose one by its full ID.")
            showDrafts(matching, notify)
            notify("Use /squid submit <draft-id> to resume exactly one draft.")
            return
        }
        if (matching.size == 1) {
            resumeDraft(playerId, session, client, manifest, matching.single(), notify)
            return
        }
        val negotiation = FormCapabilityNegotiator.negotiate(
            manifest,
            target,
            origin.wireValue,
            clientCapabilities(),
        )
        if (!negotiation.compatible) {
            finishSubmit(playerId, session)
            notify("This Minecraft client cannot safely edit all required fields in the current form.")
            return
        }
        val pending = runCatching { client.prepareCreate(target, CLIENT_CAPABILITIES) }.getOrElse { error ->
            finishSubmit(playerId, session)
            notify(safeFailure(error))
            return
        }
        synchronized(session) { session.pendingCreate = pending }
        submitPreparedCreate(
            playerId,
            session,
            client,
            manifest,
            categoryForm.label,
            pending,
            notify,
            attempt = 1,
        )
    }

    private fun resumeDraft(
        playerId: UUID,
        session: PlayerSession,
        client: SubmissionDraftClient,
        manifest: FormManifest,
        summary: DraftSummary,
        notify: (String) -> Unit,
    ) {
        if (summary.status == "processing") {
            finishSubmit(playerId, session)
            notify("Draft ${summary.id} is still processing and cannot be edited.")
            return
        }
        if (!canEdit(manifest, summary)) {
            finishSubmit(playerId, session)
            notify("Draft ${summary.id} uses a form or required feature this client cannot safely edit.")
            return
        }
        val operation = runCatching { client.getDraft(UUID.fromString(summary.id)) }.getOrElse { error ->
            finishSubmit(playerId, session)
            notify(safeFailure(error))
            return
        }
        operation.whenComplete { draft, failure ->
            dispatch {
                if (failure != null) {
                    finishSubmit(playerId, session)
                    notify(safeFailure(failure))
                    return@dispatch
                }
                val stored = checkNotNull(draft)
                if (
                    stored.schemaId != summary.schemaId ||
                    stored.schemaRevision != summary.schemaRevision ||
                    stored.category != summary.category ||
                    stored.revision != summary.revision ||
                    stored.status != summary.status ||
                    stored.status !in EDITABLE_DRAFT_STATUSES
                ) {
                    clearCurrentDraft(session, summary.id)
                    finishSubmit(playerId, session)
                    notify("The draft changed state while it was being resumed. Run /squid submit to refresh.")
                    return@dispatch
                }
                synchronized(session) {
                    session.manifest = manifest
                    session.draft = stored
                    session.busy = false
                }
                val label = manifest.categories.single { it.code == stored.category }.label
                notify("Resumed $label draft ${stored.id} at revision ${stored.revision}.")
                notify("Use /squid set <field> <value>, /squid unset <field>, and /squid status.")
            }
        }
    }

    private fun submitPreparedCreate(
        playerId: UUID,
        session: PlayerSession,
        client: SubmissionDraftClient,
        manifest: FormManifest,
        categoryLabel: String,
        pending: PendingDraftCreate,
        notify: (String) -> Unit,
        attempt: Int,
    ) {
        val operation = runCatching { client.submitCreate(pending) }.getOrElse { error ->
            finishSubmit(playerId, session)
            notify(safeFailure(error))
            return
        }
        operation.whenComplete { draft, failure ->
            if (failure != null && retryableCreateFailure(failure) && attempt < MAX_CREATE_ATTEMPTS) {
                scheduler.schedule(
                    {
                        dispatch {
                            submitPreparedCreate(
                                playerId,
                                session,
                                client,
                                manifest,
                                categoryLabel,
                                pending,
                                notify,
                                attempt = attempt + 1,
                            )
                        }
                    },
                    createRetryDelaySeconds(failure),
                    TimeUnit.SECONDS,
                )
                return@whenComplete
            }
            dispatch {
                if (failure != null) {
                    if (!retryableCreateFailure(failure)) {
                        clearPendingCreate(session, pending)
                    }
                    finishSubmit(playerId, session)
                    if (retryableCreateFailure(failure)) {
                        notify(
                            "Draft creation could not be confirmed after bounded retries. " +
                                "Run /squid submit ${pending.category} to retry the same request safely.",
                        )
                    } else {
                        notify(safeFailure(failure))
                    }
                    return@dispatch
                }
                val stored = checkNotNull(draft)
                clearPendingCreate(session, pending)
                if (
                    stored.schemaId != manifest.schemaId ||
                    stored.schemaRevision != manifest.revision ||
                    stored.status !in EDITABLE_DRAFT_STATUSES
                ) {
                    finishSubmit(playerId, session)
                    notify("The created draft uses a form or state this client cannot safely edit.")
                    return@dispatch
                }
                synchronized(session) {
                    session.manifest = manifest
                    session.draft = stored
                    session.busy = false
                }
                notify("Created $categoryLabel draft ${stored.id} at revision ${stored.revision}.")
                notify("Use /squid set <field> <value>, /squid unset <field>, and /squid status.")
            }
        }
    }

    private fun finishDiscovery(
        playerId: UUID,
        session: PlayerSession,
        manifest: FormManifest,
        summaries: List<DraftSummary>,
        notify: (String) -> Unit,
    ) {
        finishSubmit(playerId, session)
        if (summaries.isNotEmpty()) {
            showDrafts(summaries, notify)
            notify("Use /squid submit <draft-id> to resume one, or choose a category without an active draft.")
        }
        notify("Build categories: ${manifest.categories.joinToString { "${it.code} (${it.label})" }}")
        notify("Use /squid submit <category> to create or resume a synchronized draft.")
    }

    private fun finishSubmit(playerId: UUID, session: PlayerSession) {
        synchronized(session) { session.busy = false }
        removeIfIdle(playerId, session)
    }

    private fun clearPendingCreate(session: PlayerSession, pending: PendingDraftCreate) {
        synchronized(session) {
            if (session.pendingCreate === pending) {
                session.pendingCreate = null
            }
        }
    }

    private fun clearCurrentDraft(session: PlayerSession, draftId: String) {
        val expectedId = UUID.fromString(draftId)
        synchronized(session) {
            if (session.draft?.let { UUID.fromString(it.id) == expectedId } == true) {
                session.draft = null
                session.manifest = null
            }
        }
    }

    private fun showDrafts(summaries: List<DraftSummary>, notify: (String) -> Unit) {
        notify("Active synchronized drafts:")
        summaries.forEach { summary ->
            val name = summary.displayName?.let(::safeChatText)?.takeIf(String::isNotBlank)?.let { " “$it”" }.orEmpty()
            notify("${summary.id} — ${summary.category}$name, ${summary.status}, revision ${summary.revision}.")
        }
    }

    private fun canEdit(manifest: FormManifest, summary: DraftSummary): Boolean =
        summary.status in EDITABLE_DRAFT_STATUSES &&
            summary.schemaId == manifest.schemaId &&
            summary.schemaRevision == manifest.revision &&
            manifest.categories.any { it.code == summary.category } &&
            FormCapabilityNegotiator.negotiate(
                manifest,
                summary.category,
                origin.wireValue,
                clientCapabilities(),
            ).compatible

    private fun matchesCurrent(manifest: FormManifest, draft: StoredDraft, summary: DraftSummary): Boolean =
        canEdit(manifest, summary) &&
            draft.schemaId == summary.schemaId &&
            draft.schemaRevision == summary.schemaRevision &&
            draft.category == summary.category &&
            draft.revision == summary.revision &&
            draft.status == summary.status &&
            draft.origin == summary.origin

    private fun retryableCreateFailure(error: Throwable): Boolean = when (val cause = unwrap(error)) {
        is BackendTransportException -> true
        is BackendApiException -> cause.code.equals(IDEMPOTENCY_IN_PROGRESS_CODE, ignoreCase = true)
        else -> false
    }

    private fun createRetryDelaySeconds(error: Throwable): Long =
        (unwrap(error) as? BackendApiException)?.retryAfterSeconds?.coerceIn(1, MAX_CREATE_RETRY_DELAY_SECONDS)
            ?: DEFAULT_CREATE_RETRY_DELAY_SECONDS

    private fun mutateField(
        playerId: UUID,
        locale: String,
        fieldId: String,
        rawValue: String?,
        unset: Boolean,
        notify: (String) -> Unit,
    ) {
        val session = synchronized(sessions) { sessions[playerId] }
        if (session == null) {
            notify("No synchronized draft is active. Use /squid submit <category> first.")
            return
        }
        val pair = synchronized(session) {
            if (session.busy) {
                notify("A Redstone Squid draft operation is already in progress.")
                return
            }
            val draft = session.draft
            val manifest = session.manifest
            if (draft == null || manifest == null) {
                notify("No synchronized draft is active. Use /squid submit <category> first.")
                return
            }
            session.busy = true
            draft to manifest
        }
        val (draft, manifest) = pair
        val field = runCatching {
            manifest.fieldsFor(draft.category, origin.wireValue).single { it.id == fieldId }
        }.getOrElse {
            synchronized(session) { session.busy = false }
            notify("Unknown field '$fieldId' for this draft. Use /squid status to see missing required fields.")
            return
        }
        val client = draftClient(playerId, locale)
        if (unset) {
            applyMutation(session, client, draft, DraftFieldMutation.Unset(field.id), notify)
            return
        }
        val optionSource = field.optionSource
        if (optionSource == null) {
            parseAndApply(session, client, draft, field, checkNotNull(rawValue), null, notify)
            return
        }
        client.options(optionSource, draft.category).whenComplete { optionSet, failure ->
            dispatch {
                if (failure != null) {
                    synchronized(session) { session.busy = false }
                    notify(safeFailure(failure))
                } else {
                    parseAndApply(session, client, draft, field, checkNotNull(rawValue), optionSet?.options, notify)
                }
            }
        }
    }

    private fun parseAndApply(
        session: PlayerSession,
        client: SubmissionDraftClient,
        draft: StoredDraft,
        field: FormField,
        rawValue: String,
        dynamicOptions: List<com.redstonesquid.minecraft.protocol.ChoiceOption>?,
        notify: (String) -> Unit,
    ) {
        val value = runCatching { DraftFieldValueParser.parse(field, rawValue, dynamicOptions) }.getOrElse { error ->
            synchronized(session) { session.busy = false }
            notify(error.message ?: "That value is not valid for ${field.id}.")
            return
        }
        applyMutation(session, client, draft, DraftFieldMutation.Set(field.id, value), notify)
    }

    private fun applyMutation(
        session: PlayerSession,
        client: SubmissionDraftClient,
        draft: StoredDraft,
        mutation: DraftFieldMutation,
        notify: (String) -> Unit,
    ) {
        val pending = runCatching { client.prepareChange(draft, listOf(mutation)) }.getOrElse { error ->
            synchronized(session) { session.busy = false }
            notify(safeFailure(error))
            return
        }
        submitPreparedChange(session, client, pending, mutation.fieldId, notify, retryTransport = true)
    }

    private fun submitPreparedChange(
        session: PlayerSession,
        client: SubmissionDraftClient,
        pending: PendingDraftChange,
        fieldId: String,
        notify: (String) -> Unit,
        retryTransport: Boolean,
    ) {
        client.submitChange(pending).whenComplete { response, failure ->
            if (failure != null && retryTransport && unwrap(failure) is BackendTransportException) {
                scheduler.schedule(
                    { submitPreparedChange(session, client, pending, fieldId, notify, retryTransport = false) },
                    1,
                    TimeUnit.SECONDS,
                )
                return@whenComplete
            }
            dispatch {
                synchronized(session) {
                    session.busy = false
                    if (response != null) {
                        session.draft = response.draft
                    }
                }
                if (failure == null) {
                    notify("Saved '$fieldId' at draft revision ${checkNotNull(response).draft.revision}.")
                } else {
                    notify(safeFailure(failure))
                }
            }
        }
    }

    private fun schedulePoll(
        playerId: UUID,
        session: PlayerSession,
        pending: PendingPlayerAuthorization,
        notify: (String) -> Unit,
        delaySeconds: Long,
    ) {
        val remaining = pending.expiresAt.epochSecond - clock.instant().epochSecond
        if (remaining <= 0) {
            dispatch { finishExpired(playerId, session, pending, notify) }
            return
        }
        scheduler.schedule(
            { poll(playerId, session, pending, notify) },
            delaySeconds.coerceAtMost(remaining).coerceAtLeast(1),
            TimeUnit.SECONDS,
        )
    }

    private fun poll(
        playerId: UUID,
        session: PlayerSession,
        pending: PendingPlayerAuthorization,
        notify: (String) -> Unit,
    ) {
        if (clock.instant() >= pending.expiresAt) {
            dispatch { finishExpired(playerId, session, pending, notify) }
            return
        }
        val stillCurrent = synchronized(session) { session.pendingAuthorization?.challengeId == pending.challengeId }
        if (!stillCurrent) {
            return
        }
        val exchange: CompletableFuture<AuthorizedPlayer> = when (pending) {
            is PendingPaperAuthorization -> authorization.exchangePaper(pending)
            is PendingFabricAuthorization -> authorization.exchangeFabric(pending)
        }
        exchange.whenComplete { authorized, failure ->
            if (failure == null) {
                dispatch {
                    synchronized(session) { session.pendingAuthorization = null }
                    removeIfIdle(playerId, session)
                    notify("Redstone Squid account linked for $playerId until ${authorized.expiresAt}.")
                }
                return@whenComplete
            }
            val cause = unwrap(failure)
            val api = cause as? BackendApiException
            val retryable = cause is BackendTransportException ||
                api?.minecraftAuthCode == "authorization_pending" || api?.statusCode == 429
            if (retryable) {
                val retryAfter = maxOf(
                    api?.retryAfterSeconds ?: 0,
                    pending.pollingIntervalSeconds.toLong(),
                )
                schedulePoll(playerId, session, pending, notify, retryAfter)
            } else {
                dispatch {
                    synchronized(session) { session.pendingAuthorization = null }
                    removeIfIdle(playerId, session)
                    notify(safeFailure(cause))
                }
            }
        }
    }

    private fun finishExpired(
        playerId: UUID,
        session: PlayerSession,
        pending: PendingPlayerAuthorization,
        notify: (String) -> Unit,
    ) {
        synchronized(session) {
            if (session.pendingAuthorization?.challengeId == pending.challengeId) {
                session.pendingAuthorization = null
            }
        }
        removeIfIdle(playerId, session)
        notify("Redstone Squid authorization expired. Use /squid link to start again.")
    }

    private fun requireGrant(playerId: UUID, notify: (String) -> Unit): Boolean {
        val grant = runCatching { secretStore.loadPlayerGrant(grantKey(playerId)) }.getOrNull()
        if (grant == null || !grant.isUsable(clock.instant())) {
            notify("Redstone Squid account is not linked. Use /squid link first.")
            return false
        }
        return true
    }

    private fun draftClient(playerId: UUID, locale: String): SubmissionDraftClient = SubmissionDraftClient(
        transport = transport,
        secretStore = secretStore,
        grantKey = grantKey(playerId),
        clientInstanceId = "minecraft-${origin.wireValue}:$instanceId",
        locale = locale.take(64).ifBlank { "en-US" },
        paperInstallationKey = paperInstallationKey,
        clock = clock,
    )

    private fun grantKey(playerId: UUID): PlayerGrantKey {
        val installationId = paperInstallationKey?.let { key ->
            secretStore.loadInstallation(key)?.installationId
                ?: throw MissingMinecraftCredentialException("Paper installation")
        }
        return PlayerGrantKey(playerId, origin, installationId)
    }

    private fun clientCapabilities(): ClientCapabilities = ClientCapabilities(
        protocolVersion = CURRENT_PROTOCOL_VERSION,
        clientInstanceId = "minecraft-${origin.wireValue}:$instanceId",
        locale = "en-US",
        supportedControls = SUPPORTED_CONTROLS,
        capabilities = CLIENT_CAPABILITIES,
    )

    private fun session(playerId: UUID, notify: (String) -> Unit): PlayerSession? = synchronized(sessions) {
        sessions[playerId]?.let { return@synchronized it }
        if (sessions.size >= maxPlayerSessions) {
            notify("This server has reached its bounded Redstone Squid session limit. Try again later.")
            return@synchronized null
        }
        PlayerSession().also { sessions[playerId] = it }
    }

    private fun removeIfIdle(playerId: UUID, session: PlayerSession) {
        synchronized(sessions) {
            if (sessions[playerId] !== session) {
                return
            }
            val idle = synchronized(session) {
                !session.linking &&
                    !session.busy &&
                    session.pendingAuthorization == null &&
                    session.pendingCreate == null &&
                    session.draft == null
            }
            if (idle) {
                sessions.remove(playerId)
            }
        }
    }

    internal fun activeSessionCount(): Int = synchronized(sessions) { sessions.size }

    private fun dispatch(action: () -> Unit) {
        runCatching { callbackExecutor.execute { action() } }
    }

    private fun safeFailure(error: Throwable): String = when (val cause = unwrap(error)) {
        is MissingMinecraftCredentialException, is ExpiredMinecraftGrantException ->
            "Redstone Squid authorization is unavailable or expired. Use /squid link."
        is BackendApiException -> when {
            cause.minecraftAuthCode == "challenge_expired" ->
                "Redstone Squid authorization expired. Use /squid link to start again."
            cause.statusCode == 401 || cause.statusCode == 403 ->
                "Redstone Squid rejected these credentials. Link the account again or contact the server operator."
            cause.statusCode == 409 ->
                "The synchronized draft changed elsewhere. Run /squid submit to refresh the active draft list."
            cause.statusCode == 429 -> "Redstone Squid is rate limiting requests. Try again shortly."
            else -> "Redstone Squid rejected the request (HTTP ${cause.statusCode})."
        }
        is IllegalArgumentException -> cause.message ?: "Redstone Squid rejected an invalid local value."
        else -> "Redstone Squid could not reach the backend safely. Try again later."
    }

    private fun missingRequiredFields(manifest: FormManifest, draft: StoredDraft): List<String> =
        manifest.fieldsFor(draft.category, origin.wireValue)
            .filter { it.required && visible(it, draft.answers) && it.id !in draft.answers }
            .map(FormField::id)

    private fun visible(field: FormField, answers: Map<String, JsonElement>): Boolean {
        val rule = field.visibleWhen ?: return true
        val actual = answers[rule.fieldId]
        return when (rule.operator) {
            "equals" -> actual == rule.value
            "not_equals" -> actual != rule.value
            "in" -> (rule.value as? JsonArray)?.contains(actual) == true
            else -> false
        }
    }

    private class PlayerSession {
        var linking: Boolean = false
        var busy: Boolean = false
        var pendingAuthorization: PendingPlayerAuthorization? = null
        var pendingCreate: PendingDraftCreate? = null
        var manifest: FormManifest? = null
        var draft: StoredDraft? = null
    }

    private companion object {
        val SUPPORTED_CONTROLS: Set<String> = setOf("text", "number", "choice", "multi_choice", "duration", "boolean")
        val CLIENT_CAPABILITIES: Set<String> = setOf("repeatable_text")
        val EDITABLE_DRAFT_STATUSES: Set<String> = setOf("editing", "needs_attention")
        const val MAX_CREATE_ATTEMPTS: Int = 4
        const val DEFAULT_CREATE_RETRY_DELAY_SECONDS: Long = 1
        const val MAX_CREATE_RETRY_DELAY_SECONDS: Long = 5
        const val IDEMPOTENCY_IN_PROGRESS_CODE: String = "IDEMPOTENCY_IN_PROGRESS"

        fun canonicalUuid(value: String): UUID? = runCatching { UUID.fromString(value) }
            .getOrNull()
            ?.takeIf { it.toString().equals(value, ignoreCase = true) }

        fun safeChatText(value: String): String = value.map { character ->
            if (character.isISOControl()) ' ' else character
        }.joinToString("").trim()

        fun unwrap(error: Throwable): Throwable {
            var current = error
            repeat(8) {
                val cause = current.cause
                if (
                    (current is CompletionException || current is java.util.concurrent.ExecutionException) &&
                    cause != null
                ) {
                    current = cause
                } else {
                    return current
                }
            }
            return current
        }

        fun daemonScheduler(): ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { task ->
            Thread(task, "redstone-squid-device-poll").apply { isDaemon = true }
        }
    }
}
