package com.redstonesquid.minecraft.protocol

import java.time.Instant
import java.util.UUID
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

public const val CURRENT_PROTOCOL_VERSION: Int = 1
public const val MAX_FORM_MANIFEST_BYTES: Int = 256 * 1024
public const val MAX_DRAFT_LIST_BYTES: Int = 64 * 1024
public const val MAX_DISCOVERED_DRAFTS: Int = 10

private val stableIdPattern = Regex("[a-z][a-z0-9_]{0,63}")
private val schemaIdPattern = Regex("[a-z][a-z0-9_.-]{0,127}")
private val clientInstanceIdPattern = Regex("[A-Za-z0-9_.:-]{1,128}")
private val activeDraftStatuses = setOf("editing", "processing", "needs_attention")
private val submissionOrigins = setOf("discord", "web", "paper", "fabric")

/** Capabilities of a concrete renderer; this value is not sent as one object. */
public data class ClientCapabilities(
    public val protocolVersion: Int,
    public val clientInstanceId: String,
    public val locale: String,
    public val supportedControls: Set<String>,
    public val capabilities: Set<String> = emptySet(),
) {
    init {
        require(protocolVersion > 0) { "protocolVersion must be positive" }
        require(clientInstanceIdPattern.matches(clientInstanceId)) { "clientInstanceId has an invalid format" }
        require(locale.isNotBlank()) { "locale must not be blank" }
        require(supportedControls.all(stableIdPattern::matches)) { "supportedControls contains an invalid code" }
        require(capabilities.all(stableIdPattern::matches)) { "capabilities contains an invalid code" }
    }
}

@Serializable
public data class ChoiceOption(
    public val value: String,
    public val label: String,
) {
    init {
        requireStableId(value, "choice value")
        require(label.isNotBlank()) { "choice label must not be blank" }
    }
}

@Serializable
public data class VisibilityRule(
    @SerialName("field_id")
    public val fieldId: String,
    public val operator: String,
    public val value: JsonElement,
) {
    init {
        requireStableId(fieldId, "visibility field ID")
        require(operator in VISIBILITY_OPERATORS) { "unsupported visibility operator: $operator" }
    }

    private companion object {
        private val VISIBILITY_OPERATORS = setOf("equals", "not_equals", "in")
    }
}

@Serializable
public data class FieldConstraints(
    public val minimum: Double? = null,
    public val maximum: Double? = null,
    @SerialName("min_length")
    public val minLength: Int? = null,
    @SerialName("max_length")
    public val maxLength: Int? = null,
    @SerialName("min_items")
    public val minItems: Int? = null,
    @SerialName("max_items")
    public val maxItems: Int? = null,
    @SerialName("must_equal")
    public val mustEqual: JsonElement? = null,
) {
    init {
        require(minimum == null || maximum == null || minimum <= maximum) { "minimum exceeds maximum" }
        require(minLength == null || minLength >= 0) { "min_length must not be negative" }
        require(maxLength == null || maxLength >= 0) { "max_length must not be negative" }
        require(minItems == null || minItems >= 0) { "min_items must not be negative" }
        require(maxItems == null || maxItems >= 0) { "max_items must not be negative" }
        require(minLength == null || maxLength == null || minLength <= maxLength) {
            "min_length exceeds max_length"
        }
        require(minItems == null || maxItems == null || minItems <= maxItems) { "min_items exceeds max_items" }
    }
}

/**
 * A server-authored field. Control/value/origin codes remain strings so an
 * older client can report a newer required feature instead of failing decode.
 */
@Serializable
public data class FormField(
    public val id: String,
    public val label: String,
    public val control: String,
    @SerialName("value_kind")
    public val valueKind: String,
    public val required: Boolean = false,
    @SerialName("help_text")
    public val helpText: String? = null,
    public val constraints: FieldConstraints = FieldConstraints(),
    public val options: List<ChoiceOption> = emptyList(),
    @SerialName("option_source")
    public val optionSource: String? = null,
    @SerialName("visible_when")
    public val visibleWhen: VisibilityRule? = null,
    public val default: JsonElement? = null,
    public val repeatable: Boolean = false,
    @SerialName("required_capability")
    public val requiredCapability: String? = null,
    public val origins: List<String>,
) {
    init {
        requireStableId(id, "field ID")
        require(label.isNotBlank()) { "field label must not be blank" }
        requireStableId(control, "control")
        requireStableId(valueKind, "value kind")
        require(requiredCapability == null || stableIdPattern.matches(requiredCapability)) {
            "required_capability has an invalid format"
        }
        require(options.map(ChoiceOption::value).distinct().size == options.size) { "option values must be unique" }
        require(origins.isNotEmpty()) { "field must apply to at least one origin" }
        require(origins.all(stableIdPattern::matches)) { "origins contains an invalid code" }
    }

    public fun appliesTo(origin: String): Boolean = origin in origins
}

@Serializable
public data class FormSection(
    public val id: String,
    public val title: String,
    public val fields: List<FormField>,
) {
    init {
        requireStableId(id, "section ID")
        require(title.isNotBlank()) { "section title must not be blank" }
        require(fields.map(FormField::id).distinct().size == fields.size) { "section field IDs must be unique" }
    }
}

@Serializable
public data class CategoryForm(
    public val code: String,
    public val label: String,
    public val sections: List<FormSection>,
) {
    init {
        requireStableId(code, "category code")
        require(label.isNotBlank()) { "category label must not be blank" }
        require(sections.map(FormSection::id).distinct().size == sections.size) {
            "category section IDs must be unique"
        }
    }

    public val fields: List<FormField>
        get() = sections.flatMap(FormSection::fields)
}

/** Exact response shape of `GET /v1/submissions/form/options/{source}`. */
@Serializable
public data class FormOptionSet(
    public val source: String,
    public val category: String,
    public val revision: Int,
    public val options: List<ChoiceOption>,
) {
    init {
        requireStableId(source, "option source")
        requireStableId(category, "category")
        require(revision > 0) { "revision must be positive" }
        require(options.map(ChoiceOption::value).distinct().size == options.size) { "option values must be unique" }
    }
}

/** Exact response shape of `GET /v1/submissions/form/current`. */
@Serializable
public data class FormManifest(
    @SerialName("schema_id")
    public val schemaId: String,
    public val revision: Int,
    @SerialName("minimum_protocol")
    public val minimumProtocol: Int,
    @SerialName("maximum_protocol")
    public val maximumProtocol: Int,
    @SerialName("common_sections")
    public val commonSections: List<FormSection>,
    public val categories: List<CategoryForm>,
) {
    init {
        require(schemaId.matches(Regex("[a-z][a-z0-9_.-]{0,127}"))) { "schema_id has an invalid format" }
        require(revision > 0) { "revision must be positive" }
        require(minimumProtocol > 0 && maximumProtocol >= minimumProtocol) {
            "protocol bounds must be positive and ordered"
        }
        require(commonSections.map(FormSection::id).distinct().size == commonSections.size) {
            "common section IDs must be unique"
        }
        require(categories.map(CategoryForm::code).distinct().size == categories.size) {
            "category codes must be unique"
        }
    }

    public fun fieldsFor(categoryCode: String, origin: String): List<FormField> {
        val category = categories.singleOrNull { it.code == categoryCode }
            ?: throw IllegalArgumentException("unknown category: $categoryCode")
        return (commonSections.flatMap(FormSection::fields) + category.fields).filter { it.appliesTo(origin) }
    }
}

public data class CapabilityNegotiation(
    public val protocolCompatible: Boolean,
    public val missingCapabilities: List<String>,
    public val unsupportedRequiredFields: List<String>,
) {
    public val compatible: Boolean
        get() = protocolCompatible && missingCapabilities.isEmpty() && unsupportedRequiredFields.isEmpty()
}

public object FormCapabilityNegotiator {
    public fun negotiate(
        manifest: FormManifest,
        category: String,
        origin: String,
        client: ClientCapabilities,
    ): CapabilityNegotiation {
        val requiredFields = manifest.fieldsFor(category, origin).filter(FormField::required)
        val missingCapabilities = requiredFields
            .mapNotNull(FormField::requiredCapability)
            .filterNot(client.capabilities::contains)
            .distinct()
            .sorted()
        val unsupportedRequiredFields = requiredFields
            .filter { it.control !in client.supportedControls }
            .map(FormField::id)
            .sorted()

        return CapabilityNegotiation(
            protocolCompatible = client.protocolVersion in manifest.minimumProtocol..manifest.maximumProtocol,
            missingCapabilities = missingCapabilities,
            unsupportedRequiredFields = unsupportedRequiredFields,
        )
    }
}

/** Exact request shape of `POST /v1/submissions/drafts`. */
@Serializable
public data class DraftCreateRequest(
    public val category: String,
    public val origin: String,
    @SerialName("client_capabilities")
    public val clientCapabilities: Set<String> = emptySet(),
) {
    init {
        requireStableId(category, "category")
        requireStableId(origin, "origin")
        require(clientCapabilities.size <= 64) { "client_capabilities exceeds 64 entries" }
        require(clientCapabilities.all(stableIdPattern::matches)) {
            "client_capabilities contains an invalid code"
        }
    }
}

@Serializable
public data class FieldOperationRequest(
    @SerialName("operation_id")
    public val operationId: String,
    @SerialName("field_id")
    public val fieldId: String,
    public val kind: String,
    public val value: JsonElement? = null,
) {
    init {
        require(runCatching { UUID.fromString(operationId) }.isSuccess) { "operation_id must be a UUID" }
        requireStableId(fieldId, "field_id")
        require(kind == "set" || kind == "unset") { "kind must be set or unset" }
        require(kind != "unset" || value == null) { "unset operations cannot carry a value" }
    }
}

/** Exact request shape of `POST /v1/submissions/drafts/{id}/changes`. */
@Serializable
public data class DraftChangeRequest(
    @SerialName("base_revision")
    public val baseRevision: Long,
    @SerialName("client_instance_id")
    public val clientInstanceId: String,
    @SerialName("idempotency_key")
    public val idempotencyKey: String,
    public val operations: List<FieldOperationRequest>,
) {
    init {
        require(baseRevision >= 0) { "base_revision must not be negative" }
        require(clientInstanceIdPattern.matches(clientInstanceId)) { "client_instance_id has an invalid format" }
        require(idempotencyKey.length in 8..255 && idempotencyKey.all { it.code in 0x21..0x7E }) {
            "idempotency_key must be 8-255 visible ASCII characters"
        }
        require(operations.size in 1..100) { "operations must contain 1-100 entries" }
        require(operations.map(FieldOperationRequest::operationId).distinct().size == operations.size) {
            "operation IDs must be unique"
        }
        require(operations.map(FieldOperationRequest::fieldId).distinct().size == operations.size) {
            "a change may mutate each field at most once"
        }
    }
}

@Serializable
public data class StoredDraft(
    public val id: String,
    @SerialName("schema_id")
    public val schemaId: String,
    @SerialName("schema_revision")
    public val schemaRevision: Int,
    public val category: String,
    public val revision: Long,
    public val status: String,
    public val answers: Map<String, JsonElement>,
    public val origin: String,
    @SerialName("created_at")
    public val createdAt: String,
    @SerialName("updated_at")
    public val updatedAt: String,
    @SerialName("expires_at")
    public val expiresAt: String,
) {
    init {
        require(runCatching { UUID.fromString(id) }.isSuccess) { "draft ID must be a UUID" }
        requireStableId(category, "category")
        require(schemaRevision > 0) { "schema_revision must be positive" }
        require(revision >= 0) { "revision must not be negative" }
        requireStableId(origin, "origin")
    }
}

/** One compact, answer-free entry from the caller's active-draft collection. */
@Serializable
public data class DraftSummary(
    public val id: String,
    @SerialName("schema_id")
    public val schemaId: String,
    @SerialName("schema_revision")
    public val schemaRevision: Int,
    public val category: String,
    public val revision: Long,
    public val status: String,
    public val origin: String,
    @SerialName("display_name")
    public val displayName: String? = null,
    @SerialName("created_at")
    public val createdAt: String,
    @SerialName("updated_at")
    public val updatedAt: String,
    @SerialName("expires_at")
    public val expiresAt: String,
) {
    init {
        requireCanonicalUuid(id, "draft summary ID")
        require(schemaIdPattern.matches(schemaId)) { "draft summary schema_id has an invalid format" }
        require(schemaRevision > 0) { "draft summary schema_revision must be positive" }
        requireStableId(category, "draft summary category")
        require(revision >= 0) { "draft summary revision must not be negative" }
        require(status in activeDraftStatuses) { "draft summary status is not active: $status" }
        require(origin in submissionOrigins) { "draft summary origin is unsupported: $origin" }
        require(
            displayName == null ||
                (displayName.isNotBlank() && displayName.codePointCount(0, displayName.length) <= 120),
        ) { "draft summary display_name must contain 1-120 characters" }
        val created = parseInstant(createdAt, "created_at")
        val updated = parseInstant(updatedAt, "updated_at")
        val expires = parseInstant(expiresAt, "expires_at")
        require(updated >= created) { "draft summary updated_at precedes created_at" }
        require(expires > updated) { "draft summary expires_at must follow updated_at" }
    }
}

/** Bounded known fields from `GET /v1/submissions/drafts`; additive v1 fields are ignored. */
@Serializable
public data class DraftListResponse(
    public val drafts: List<DraftSummary>,
) {
    init {
        require(drafts.size <= MAX_DISCOVERED_DRAFTS) {
            "draft list exceeds $MAX_DISCOVERED_DRAFTS entries"
        }
        require(drafts.map { UUID.fromString(it.id) }.distinct().size == drafts.size) {
            "draft list contains duplicate IDs"
        }
    }
}

@Serializable
public data class DraftChangeResponse(
    public val draft: StoredDraft,
    public val replayed: Boolean,
)

public object FormProtocolJson {
    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        isLenient = false
        allowSpecialFloatingPointValues = false
        allowTrailingComma = false
    }

    public fun decodeManifest(document: String): FormManifest {
        require(document.encodeToByteArray().size <= MAX_FORM_MANIFEST_BYTES) {
            "form manifest exceeds $MAX_FORM_MANIFEST_BYTES bytes"
        }
        return json.decodeFromString<FormManifest>(document)
    }

    public fun decodeOptions(document: String): FormOptionSet = json.decodeFromString(document)

    public fun decodeDraftList(document: String): DraftListResponse {
        require(document.encodeToByteArray().size <= MAX_DRAFT_LIST_BYTES) {
            "draft list exceeds $MAX_DRAFT_LIST_BYTES bytes"
        }
        return json.decodeFromString(document)
    }

    public fun encodeDraftCreate(request: DraftCreateRequest): String = json.encodeToString(request)

    public fun encodeDraftChange(request: DraftChangeRequest): String = json.encodeToString(request)

    public fun decodeStoredDraft(document: String): StoredDraft = json.decodeFromString(document)

    public fun decodeDraftChange(document: String): DraftChangeResponse = json.decodeFromString(document)
}

private fun parseInstant(value: String, name: String): Instant = runCatching { Instant.parse(value) }
    .getOrElse { throw IllegalArgumentException("draft summary $name is not an ISO-8601 instant", it) }

private fun requireCanonicalUuid(value: String, name: String): UUID {
    val parsed = runCatching { UUID.fromString(value) }
        .getOrElse { throw IllegalArgumentException("$name must be a UUID", it) }
    require(parsed.toString().equals(value, ignoreCase = true)) { "$name must use canonical UUID syntax" }
    return parsed
}

private fun requireStableId(value: String, name: String) {
    require(stableIdPattern.matches(value)) { "$name has an invalid format: $value" }
}
