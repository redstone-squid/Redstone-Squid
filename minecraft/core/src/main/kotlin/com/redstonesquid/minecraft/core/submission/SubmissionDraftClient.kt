package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.core.auth.ExpiredMinecraftGrantException
import com.redstonesquid.minecraft.core.auth.MinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.MissingMinecraftCredentialException
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.auth.PlayerGrantKey
import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.BackendRequest
import com.redstonesquid.minecraft.core.http.BackendTransport
import com.redstonesquid.minecraft.core.http.executeEmpty
import com.redstonesquid.minecraft.core.http.executeJson
import com.redstonesquid.minecraft.protocol.DraftChangeRequest
import com.redstonesquid.minecraft.protocol.DraftChangeResponse
import com.redstonesquid.minecraft.protocol.DraftCreateRequest
import com.redstonesquid.minecraft.protocol.FieldOperationRequest
import com.redstonesquid.minecraft.protocol.FormManifest
import com.redstonesquid.minecraft.protocol.FormOptionSet
import com.redstonesquid.minecraft.protocol.FormProtocolJson
import com.redstonesquid.minecraft.protocol.MAX_FORM_MANIFEST_BYTES
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.StoredDraft
import com.redstonesquid.minecraft.protocol.SubmissionApiPaths
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.time.Clock
import java.util.UUID
import java.util.concurrent.CompletableFuture
import kotlinx.serialization.json.JsonElement

private const val MAX_DRAFT_RESPONSE_BYTES: Int = 256 * 1024
private val stableIdPattern = Regex("[a-z][a-z0-9_]{0,63}")
private val clientInstanceIdPattern = Regex("[A-Za-z0-9_.:-]{1,128}")

public sealed interface DraftFieldMutation {
    public val fieldId: String

    public data class Set(override val fieldId: String, public val value: JsonElement) : DraftFieldMutation

    public data class Unset(override val fieldId: String) : DraftFieldMutation
}

/** A retry-safe optimistic edit. Reuse this object after uncertain network failures. */
public class PendingDraftChange internal constructor(
    public val draftId: UUID,
    public val baseRevision: Long,
    internal val request: DraftChangeRequest,
) {
    override fun toString(): String =
        "PendingDraftChange(draftId=$draftId, baseRevision=$baseRevision, request=<redacted>)"
}

/**
 * Typed synchronized-draft client bound to one server-issued player grant.
 * Origin comes from that grant key; no operation accepts an account ID or origin.
 */
public class SubmissionDraftClient(
    private val transport: BackendTransport,
    private val secretStore: MinecraftSecretStore,
    private val grantKey: PlayerGrantKey,
    private val clientInstanceId: String,
    private val locale: String,
    private val paperInstallationKey: PaperInstallationKey? = null,
    private val clock: Clock = Clock.systemUTC(),
) {
    init {
        require(clientInstanceIdPattern.matches(clientInstanceId)) { "client instance ID has an invalid format" }
        require(locale.isNotBlank() && locale.length <= 64 && locale.none { it == '\r' || it == '\n' }) {
            "locale has an invalid format"
        }
        require((grantKey.origin == MinecraftOrigin.PAPER) == (paperInstallationKey != null)) {
            "Paper sessions require an installation key and Fabric sessions must not have one"
        }
    }

    public fun currentForm(): CompletableFuture<FormManifest> = transport.executeJson(
        BackendRequest(
            method = BackendHttpMethod.GET,
            pathAndQuery = SubmissionApiPaths.CURRENT_FORM,
            headers = localeHeaders(),
            maxResponseBytes = MAX_FORM_MANIFEST_BYTES,
        ),
        "submission form manifest",
        FormProtocolJson::decodeManifest,
    )

    public fun options(source: String, category: String): CompletableFuture<FormOptionSet> {
        requireStableId(source, "option source")
        requireStableId(category, "category")
        val path = SubmissionApiPaths.FORM_OPTIONS_TEMPLATE.replace("{source}", source) +
            "?category=${encodeQuery(category)}"
        return transport.executeJson(
            BackendRequest(
                method = BackendHttpMethod.GET,
                pathAndQuery = path,
                headers = localeHeaders(),
                maxResponseBytes = MAX_DRAFT_RESPONSE_BYTES,
            ),
            "submission option set",
            FormProtocolJson::decodeOptions,
        )
    }

    public fun createDraft(
        category: String,
        clientCapabilities: Set<String> = emptySet(),
    ): CompletableFuture<StoredDraft> {
        val body = FormProtocolJson.encodeDraftCreate(
            DraftCreateRequest(
                category = category,
                origin = grantKey.origin.wireValue,
                clientCapabilities = clientCapabilities,
            ),
        )
        return transport.executeJson(
            authenticatedRequest(BackendHttpMethod.POST, SubmissionApiPaths.DRAFTS, body),
            "created submission draft",
            FormProtocolJson::decodeStoredDraft,
        ).thenApply { draft -> validateDraft(draft, expectedCategory = category) }
    }

    public fun getDraft(draftId: UUID): CompletableFuture<StoredDraft> = transport.executeJson(
        authenticatedRequest(
            BackendHttpMethod.GET,
            SubmissionApiPaths.DRAFT_TEMPLATE.replace("{id}", draftId.toString()),
        ),
        "submission draft",
        FormProtocolJson::decodeStoredDraft,
    ).thenApply { draft -> validateDraft(draft, expectedId = draftId) }

    public fun prepareChange(
        draft: StoredDraft,
        mutations: List<DraftFieldMutation>,
    ): PendingDraftChange {
        val draftId = UUID.fromString(draft.id)
        validateDraft(draft, expectedId = draftId)
        require(mutations.size in 1..100) { "a draft change must contain 1-100 mutations" }
        require(mutations.map(DraftFieldMutation::fieldId).distinct().size == mutations.size) {
            "a draft change may mutate each field at most once"
        }
        val operations = mutations.map { mutation ->
            requireStableId(mutation.fieldId, "field ID")
            when (mutation) {
                is DraftFieldMutation.Set -> FieldOperationRequest(
                    operationId = UUID.randomUUID().toString(),
                    fieldId = mutation.fieldId,
                    kind = "set",
                    value = mutation.value,
                )
                is DraftFieldMutation.Unset -> FieldOperationRequest(
                    operationId = UUID.randomUUID().toString(),
                    fieldId = mutation.fieldId,
                    kind = "unset",
                )
            }
        }
        val request = DraftChangeRequest(
            baseRevision = draft.revision,
            clientInstanceId = clientInstanceId,
            idempotencyKey = "$clientInstanceId:${UUID.randomUUID()}",
            operations = operations,
        )
        return PendingDraftChange(draftId, draft.revision, request)
    }

    public fun submitChange(change: PendingDraftChange): CompletableFuture<DraftChangeResponse> {
        val path = SubmissionApiPaths.DRAFT_CHANGES_TEMPLATE.replace("{id}", change.draftId.toString())
        return transport.executeJson(
            authenticatedRequest(
                BackendHttpMethod.POST,
                path,
                FormProtocolJson.encodeDraftChange(change.request),
            ),
            "submission draft change",
            FormProtocolJson::decodeDraftChange,
        ).thenApply { response ->
            response.copy(draft = validateDraft(response.draft, expectedId = change.draftId))
        }
    }

    public fun deleteDraft(draftId: UUID): CompletableFuture<Unit> = transport.executeEmpty(
        authenticatedRequest(
            BackendHttpMethod.DELETE,
            SubmissionApiPaths.DRAFT_TEMPLATE.replace("{id}", draftId.toString()),
        ),
        expectedStatus = 204,
    )

    private fun authenticatedRequest(
        method: BackendHttpMethod,
        path: String,
        body: String? = null,
    ): BackendRequest = BackendRequest(
        method = method,
        pathAndQuery = path,
        body = body,
        headers = authenticationHeaders() + localeHeaders() + mapOf(
            "Cache-Control" to "no-store",
            "Pragma" to "no-cache",
        ),
        maxResponseBytes = MAX_DRAFT_RESPONSE_BYTES,
    )

    private fun authenticationHeaders(): Map<String, String> {
        val grant = secretStore.loadPlayerGrant(grantKey)
            ?: throw MissingMinecraftCredentialException("player grant")
        if (!grant.isUsable(clock.instant())) {
            throw ExpiredMinecraftGrantException()
        }
        val headers = mutableMapOf("Authorization" to grant.authorizationHeader())
        if (grantKey.origin == MinecraftOrigin.PAPER) {
            val installation = secretStore.loadInstallation(checkNotNull(paperInstallationKey))
                ?: throw MissingMinecraftCredentialException("Paper installation")
            require(installation.installationId == grantKey.installationId) {
                "Paper installation does not match the player grant"
            }
            headers.putAll(installation.headers())
        }
        return headers
    }

    private fun localeHeaders(): Map<String, String> = mapOf("Accept-Language" to locale)

    private fun validateDraft(
        draft: StoredDraft,
        expectedId: UUID? = null,
        expectedCategory: String? = null,
    ): StoredDraft {
        require(draft.origin == grantKey.origin.wireValue) { "backend draft origin did not match the player grant" }
        require(expectedId == null || UUID.fromString(draft.id) == expectedId) {
            "backend draft ID did not match the request"
        }
        require(expectedCategory == null || draft.category == expectedCategory) {
            "backend draft category did not match the request"
        }
        return draft
    }

    private companion object {
        fun requireStableId(value: String, name: String) {
            require(stableIdPattern.matches(value)) { "$name has an invalid format" }
        }

        fun encodeQuery(value: String): String = URLEncoder.encode(value, StandardCharsets.UTF_8)
    }
}
