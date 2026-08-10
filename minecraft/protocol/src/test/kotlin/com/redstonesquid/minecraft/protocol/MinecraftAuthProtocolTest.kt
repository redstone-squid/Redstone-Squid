package com.redstonesquid.minecraft.protocol

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class MinecraftAuthProtocolTest {
    @Test
    fun `authorization paths match the v1 backend contract`() {
        assertEquals("/minecraft/auth/paper/challenges", MinecraftAuthApiPaths.PAPER_CHALLENGES)
        assertEquals("/minecraft/auth/paper/challenges/exchange", MinecraftAuthApiPaths.PAPER_EXCHANGE)
        assertEquals("/minecraft/auth/fabric/challenges", MinecraftAuthApiPaths.FABRIC_CHALLENGES)
        assertEquals("/minecraft/auth/fabric/challenges/exchange", MinecraftAuthApiPaths.FABRIC_EXCHANGE)
    }

    @Test
    fun `Paper challenge body contains only the server-observed player identity`() {
        val document = MinecraftAuthProtocolJson.encodePaperChallenge(
            PaperChallengeCreateRequest("123e4567-e89b-12d3-a456-426614174000"),
        )

        assertEquals("{\"java_uuid\":\"123e4567-e89b-12d3-a456-426614174000\"}", document)
        assertFalse("account" in document)
        assertFalse("origin" in document)
    }

    @Test
    fun `secret-bearing responses redact their string representations`() {
        val challenge = MinecraftAuthProtocolJson.decodeChallenge(
            """
            {
              "id":"123e4567-e89b-12d3-a456-426614174000",
              "device_code":"abcdefghijklmnopqrstuvwxyzABCDE_123456",
              "user_code":"ABCD-EFGH-IJKL-MNOP",
              "expires_at":"2030-01-01T00:00:00Z",
              "polling_interval_seconds":5
            }
            """.trimIndent(),
        )
        val grant = MinecraftAuthProtocolJson.decodeGrant(
            """
            {
              "grant_id":"123e4567-e89b-12d3-a456-426614174001",
              "token":"sqpt_123e4567e89b12d3a456426614174001_abcdefghijklmnopqrstuvwxyzABCDE_123456",
              "java_uuid":"123e4567-e89b-12d3-a456-426614174000",
              "origin":"fabric",
              "installation_id":null,
              "expires_at":"2030-01-01T00:05:00Z"
            }
            """.trimIndent(),
        )

        assertTrue("<redacted>" in challenge.toString())
        assertFalse("abcdefghijklmnopqrstuvwxyzABCDE_123456" in challenge.toString())
        assertTrue("<redacted>" in grant.toString())
        assertFalse("sqpt_" in grant.toString())
    }
}
