package com.redstonesquid.minecraft.paper

import org.junit.jupiter.api.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class PaperRuntimeSettingsTest {
    private val properties = mapOf(
        PaperRuntimeSettings.API_BASE_PROPERTY to "https://api.example.test/v1/",
        PaperRuntimeSettings.APPROVAL_URI_PROPERTY to "https://www.example.test/minecraft/link",
        PaperRuntimeSettings.INSTALLATION_ID_PROPERTY to "123e4567-e89b-12d3-a456-426614174010",
        "redstonesquid.installationSecret" to "property-secret-must-never-be-used-123456",
    )

    @Test
    fun `Paper secret is accepted only from the environment and is always redacted`() {
        assertFailsWith<IllegalArgumentException> {
            PaperRuntimeSettings.load(properties::get) { null }
        }
        val environmentSecret = "environment-secret-is-long-enough-123456"
        val settings = PaperRuntimeSettings.load(properties::get) { name ->
            environmentSecret.takeIf { name == PaperRuntimeSettings.INSTALLATION_SECRET_ENV }
        }

        assertTrue("installationSecret=<redacted>" in settings.toString())
        assertFalse(environmentSecret in settings.toString())
        assertFalse(properties.getValue("redstonesquid.installationSecret") in settings.toString())
    }
}
