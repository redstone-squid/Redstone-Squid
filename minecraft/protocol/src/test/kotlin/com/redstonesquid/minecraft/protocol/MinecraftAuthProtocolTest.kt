package com.redstonesquid.minecraft.protocol

import java.util.UUID
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
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
              "polling_interval_seconds":5,
              "verification_uri":"https://www.example.test/link",
              "verification_uri_complete":"https://www.example.test/link?code=ABCD-EFGH-IJKL-MNOP"
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
        assertEquals("https://www.example.test/link", challenge.verificationUri)
        assertEquals(
            "https://www.example.test/link?code=ABCD-EFGH-IJKL-MNOP",
            challenge.verificationUriComplete,
        )
        assertTrue("<redacted>" in grant.toString())
        assertFalse("sqpt_" in grant.toString())
    }

    @Test
    fun `challenge rejects a plaintext verification endpoint`() {
        assertFailsWith<IllegalArgumentException> {
            MinecraftAuthProtocolJson.decodeChallenge(
                """
                {
                  "id":"123e4567-e89b-12d3-a456-426614174000",
                  "device_code":"abcdefghijklmnopqrstuvwxyzABCDE_123456",
                  "user_code":"ABCD-EFGH-IJKL-MNOP",
                  "expires_at":"2030-01-01T00:00:00Z",
                  "polling_interval_seconds":5,
                  "verification_uri":"http://www.example.test/link"
                }
                """.trimIndent(),
            )
        }
    }

    @Test
    fun `player token grammar rejects malformed or differently bound grants`() {
        val grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174001")
        val prefix = "sqpt_123e4567e89b12d3a456426614174001_"
        val secret = "a".repeat(32)
        assertTrue(isValidPlayerToken(prefix + secret, grantId))
        assertTrue(isValidPlayerToken(prefix + "A_9-".repeat(64), grantId))
        val invalidTokens = listOf(
            prefix + "a".repeat(31),
            prefix + "a".repeat(257),
            prefix + secret + " ",
            prefix + secret + "é",
            prefix + secret + ".",
            prefix.replace("sqpt", "other") + secret,
            prefix.replace("174001", "174002") + secret,
            prefix.replace("174001", "17400z") + secret,
        )
        for (token in invalidTokens) {
            assertFalse(isValidPlayerToken(token, grantId))
        }
    }
}
