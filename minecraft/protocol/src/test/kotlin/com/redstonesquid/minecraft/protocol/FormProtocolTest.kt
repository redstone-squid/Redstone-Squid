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
}
