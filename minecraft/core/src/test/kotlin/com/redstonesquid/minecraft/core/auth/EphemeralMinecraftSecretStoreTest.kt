package com.redstonesquid.minecraft.core.auth

import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID
import org.junit.jupiter.api.Test
import kotlin.test.assertFailsWith
import kotlin.test.assertNull

class EphemeralMinecraftSecretStoreTest {
    private val now = Instant.parse("2030-01-01T00:00:00Z")

    @Test
    fun `grant capacity is bounded and expired grants are discarded`() {
        val store = EphemeralMinecraftSecretStore(
            clock = Clock.fixed(now, ZoneOffset.UTC),
            maxPlayerGrants = 1,
        )
        val first = grant("123e4567-e89b-12d3-a456-426614174001", now.plusSeconds(60))
        val second = grant("123e4567-e89b-12d3-a456-426614174002", now.plusSeconds(60))
        store.savePlayerGrant(first)

        assertFailsWith<IllegalArgumentException> { store.savePlayerGrant(second) }
        store.removePlayerGrant(first.key)
        store.savePlayerGrant(second)

        val alreadyExpired = grant("123e4567-e89b-12d3-a456-426614174003", now)
        store.removePlayerGrant(second.key)
        store.savePlayerGrant(alreadyExpired)
        assertNull(store.loadPlayerGrant(alreadyExpired.key))
    }

    private fun grant(javaUuid: String, expiresAt: Instant): PlayerGrantCredential {
        val grantId = UUID.fromString("123e4567-e89b-12d3-a456-426614174030")
        return PlayerGrantCredential(
            grantId = grantId,
            key = PlayerGrantKey(UUID.fromString(javaUuid), MinecraftOrigin.FABRIC),
            token = "sqpt_${grantId.toString().replace("-", "")}_abcdefghijklmnopqrstuvwxyzABCDE_123456",
            expiresAt = expiresAt,
        )
    }
}
