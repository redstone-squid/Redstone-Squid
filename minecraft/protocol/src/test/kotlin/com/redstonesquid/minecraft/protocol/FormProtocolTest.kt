package com.redstonesquid.minecraft.protocol

import kotlinx.serialization.json.JsonPrimitive
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class FormProtocolTest {
    @Test
    fun `submission paths match the v1 backend contract`() {
        assertEquals("/submissions/form/current", SubmissionApiPaths.CURRENT_FORM)
        assertEquals("/submissions/form/options/{source}", SubmissionApiPaths.FORM_OPTIONS_TEMPLATE)
        assertEquals("/submissions/drafts", SubmissionApiPaths.DRAFTS)
        assertEquals("/submissions/drafts/{id}", SubmissionApiPaths.DRAFT_TEMPLATE)
        assertEquals("/submissions/drafts/{id}/changes", SubmissionApiPaths.DRAFT_CHANGES_TEMPLATE)
    }

    @Test
    fun `draft and option responses have typed decoders`() {
        val options = FormProtocolJson.decodeOptions(
            """{"source":"versions","category":"door","revision":2,"options":[{"value":"java","label":"Java"}]}""",
        )
        val changed = FormProtocolJson.decodeDraftChange(
            """
            {
              "draft":{
                "id":"123e4567-e89b-12d3-a456-426614174000",
                "schema_id":"redstone_squid_submission",
                "schema_revision":1,
                "category":"door",
                "revision":2,
                "status":"editing",
                "answers":{"description":"hi"},
                "origin":"fabric",
                "created_at":"2030-01-01T00:00:00Z",
                "updated_at":"2030-01-01T00:00:01Z",
                "expires_at":"2030-01-08T00:00:00Z"
              },
              "replayed":true
            }
            """.trimIndent(),
        )

        assertEquals(2, options.revision)
        assertEquals("hi", changed.draft.answers.getValue("description").toString().trim('"'))
        assertTrue(changed.replayed)
    }

    @Test
    fun `active draft discovery is bounded validated and additive-field tolerant`() {
        val discovered = FormProtocolJson.decodeDraftList(
            """
            {
              "drafts":[${draftSummaryDocument(1, extra = ",\"future_summary_value\":true")}],
              "future_collection_value":"kept forward-compatible"
            }
            """.trimIndent(),
        )

        assertEquals(1, discovered.drafts.size)
        assertEquals("Workshop door", discovered.drafts.single().displayName)
        assertEquals("needs_attention", discovered.drafts.single().status)

        val duplicate = """{"drafts":[${draftSummaryDocument(1)},${draftSummaryDocument(1)}]}"""
        assertFailsWith<IllegalArgumentException> { FormProtocolJson.decodeDraftList(duplicate) }

        val inactive = """{"drafts":[${draftSummaryDocument(1, status = "submitted")}]}"""
        assertFailsWith<IllegalArgumentException> { FormProtocolJson.decodeDraftList(inactive) }

        val tooMany = (1..(MAX_DISCOVERED_DRAFTS + 1)).joinToString(",") { draftSummaryDocument(it) }
        assertFailsWith<IllegalArgumentException> {
            FormProtocolJson.decodeDraftList("""{"drafts":[$tooMany]}""")
        }
    }

    @Test
    fun `active draft discovery enforces its own byte budget before decoding`() {
        val oversized = " ".repeat(MAX_DRAFT_LIST_BYTES + 1)
        assertFailsWith<IllegalArgumentException> { FormProtocolJson.decodeDraftList(oversized) }
    }

    @Test
    fun `active draft discovery rejects invalid known summary values`() {
        val valid = draftSummaryDocument(1)
        val invalid = listOf(
            valid.replace("123e4567-e89b-42d3-a456-000000000001", "not-a-uuid"),
            valid.replace("123e4567-e89b-42d3-a456-000000000001", "1-1-1-1-1"),
            valid.replace("\"origin\":\"fabric\"", "\"origin\":\"unknown\""),
            valid.replace("\"created_at\":\"2030-01-01T00:00:00Z\"", "\"created_at\":\"tomorrow\""),
            valid.replace("\"expires_at\":\"2030-01-08T00:00:00Z\"", "\"expires_at\":\"2030-01-01T00:00:00Z\""),
            valid.replace("Workshop door", "x".repeat(121)),
            valid.replace("Workshop door", "   "),
        )

        invalid.forEach { summary ->
            assertFailsWith<IllegalArgumentException> {
                FormProtocolJson.decodeDraftList("""{"drafts":[$summary]}""")
            }
        }
    }

    @Test
    fun `client parses the backend manifest and blocks unknown required controls`() {
        val document = manifestDocument(
            extraField =
                """
                ,{
                  "id": "future_shape",
                  "label": "Preview",
                  "control": "voxel_preview",
                  "value_kind": "string",
                  "required": true,
                  "origins": ["fabric"],
                  "future_field_value": true
                }
                """.trimIndent(),
        )

        val manifest = FormProtocolJson.decodeManifest(document)
        val result = FormCapabilityNegotiator.negotiate(
            manifest = manifest,
            category = "door",
            origin = "fabric",
            client = ClientCapabilities(
                protocolVersion = 1,
                clientInstanceId = "fabric:test",
                locale = "en",
                supportedControls = setOf("choice"),
            ),
        )

        assertEquals("redstone_squid_submission", manifest.schemaId)
        assertFalse(result.compatible)
        assertEquals(listOf("future_shape"), result.unsupportedRequiredFields)
    }

    @Test
    fun `unsupported optional controls do not block a renderer`() {
        val manifest = FormProtocolJson.decodeManifest(manifestDocument())
        val result = FormCapabilityNegotiator.negotiate(
            manifest,
            category = "door",
            origin = "fabric",
            client = ClientCapabilities(1, "fabric:test", "en", setOf("choice")),
        )

        assertTrue(result.compatible)
    }

    @Test
    fun `required renderer capability is reported by its stable code`() {
        val manifest = FormProtocolJson.decodeManifest(
            manifestDocument(requiredCapability = "schematic_capture"),
        )
        val result = FormCapabilityNegotiator.negotiate(
            manifest,
            category = "door",
            origin = "fabric",
            client = ClientCapabilities(1, "fabric:test", "en", setOf("choice")),
        )

        assertEquals(listOf("schematic_capture"), result.missingCapabilities)
    }

    @Test
    fun `draft request envelope matches backend change semantics`() {
        val request = DraftChangeRequest(
            baseRevision = 4,
            clientInstanceId = "fabric:test",
            idempotencyKey = "retry-key-0001",
            operations = listOf(
                FieldOperationRequest(
                    operationId = "123e4567-e89b-12d3-a456-426614174000",
                    fieldId = "description",
                    kind = "set",
                    value = JsonPrimitive("hello"),
                ),
            ),
        )

        assertEquals(
            """
            {"base_revision":4,"client_instance_id":"fabric:test","idempotency_key":"retry-key-0001",\
            "operations":[{"operation_id":"123e4567-e89b-12d3-a456-426614174000",\
            "field_id":"description","kind":"set","value":"hello"}]}
            """.trimIndent().replace("\\\n", ""),
            FormProtocolJson.encodeDraftChange(request),
        )
        assertFailsWith<IllegalArgumentException> {
            request.copy(
                operations = listOf(
                    FieldOperationRequest(
                        "123e4567-e89b-12d3-a456-426614174000",
                        "description",
                        "unset",
                        JsonPrimitive("not allowed"),
                    ),
                ),
            )
        }
    }

    @Test
    fun `manifest parser enforces its byte budget before decoding`() {
        val oversized = " ".repeat(MAX_FORM_MANIFEST_BYTES + 1)
        assertFailsWith<IllegalArgumentException> { FormProtocolJson.decodeManifest(oversized) }
    }

    private fun manifestDocument(
        extraField: String = "",
        requiredCapability: String? = null,
    ): String {
        val capability = requiredCapability?.let { "\"$it\"" } ?: "null"
        return """
            {
              "schema_id": "redstone_squid_submission",
              "revision": 1,
              "minimum_protocol": 1,
              "maximum_protocol": 1,
              "future_top_level_value": 42,
              "common_sections": [
                {
                  "id": "identity",
                  "title": "Build",
                  "fields": [
                    {
                      "id": "category",
                      "label": "Category",
                      "control": "choice",
                      "value_kind": "string",
                      "required": true,
                      "required_capability": $capability,
                      "options": [{"value": "door", "label": "Door"}],
                      "origins": ["discord", "web", "paper", "fabric"]
                    }
                    $extraField
                  ]
                }
              ],
              "categories": [
                {"code": "door", "label": "Door", "sections": []}
              ]
            }
        """.trimIndent()
    }

    private fun draftSummaryDocument(
        number: Int,
        status: String = "needs_attention",
        extra: String = "",
    ): String {
        val suffix = number.toString().padStart(12, '0')
        return """
            {
              "id":"123e4567-e89b-42d3-a456-$suffix",
              "schema_id":"redstone_squid_submission",
              "schema_revision":1,
              "category":"door",
              "revision":2,
              "status":"$status",
              "origin":"fabric",
              "display_name":"Workshop door",
              "created_at":"2030-01-01T00:00:00Z",
              "updated_at":"2030-01-01T00:00:01Z",
              "expires_at":"2030-01-08T00:00:00Z"
              $extra
            }
        """.trimIndent()
    }
}
