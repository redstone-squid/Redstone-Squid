package com.redstonesquid.minecraft.core.auth

import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.time.Clock
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

private val storeKeyPattern = Regex("[A-Za-z0-9_.:-]{1,128}")

@JvmInline
public value class PaperInstallationKey(public val value: String) {
    init {
        require(storeKeyPattern.matches(value)) { "installation store key has an invalid format" }
    }
}

/** Authenticates a Paper server installation; individual players also need a [PlayerGrantCredential]. */
public class PaperInstallationCredential(
    public val installationId: UUID,
    secret: String,
) {
    private val secret: String = secret.also {
        require(it.length in 32..512 && it.isVisibleAscii()) { "installation secret has an invalid format" }
    }

    internal fun headers(): Map<String, String> = mapOf(
        "Squid-Installation-ID" to installationId.toString(),
        "Squid-Installation-Secret" to secret,
    )

    override fun toString(): String =
        "PaperInstallationCredential(installationId=$installationId, secret=<redacted>)"
}

/** Locates a player's grant for one origin, and for Paper, one server installation. */
public data class PlayerGrantKey(
    public val javaUuid: UUID,
    public val origin: MinecraftOrigin,
    public val installationId: UUID? = null,
) {
    init {
        require((origin == MinecraftOrigin.PAPER) == (installationId != null)) {
            "only Paper player grants are bound to an installation"
        }
    }
}

/** Expiring authorization to act for a linked player, issued after browser approval. */
public class PlayerGrantCredential(
    public val grantId: UUID,
    public val key: PlayerGrantKey,
    token: String,
    public val expiresAt: Instant,
) {
    private val token: String = token.also {
        require(validPlayerToken(it, grantId)) {
            "player token has an invalid format"
        }
    }

    public fun isUsable(at: Instant): Boolean = expiresAt.isAfter(at)

    internal fun authorizationHeader(): String = "Bearer $token"

    override fun toString(): String =
        "PlayerGrantCredential(grantId=$grantId, key=$key, token=<redacted>, expiresAt=$expiresAt)"
}

/**
 * Credential storage shared by game-thread calls and asynchronous HTTP completions.
 *
 * Implementations used across threads must synchronize access. Keep secrets in memory
 * or an OS credential vault; persistent implementations must encrypt at rest, replace
 * writes atomically, and keep values out of logs and unprotected backups.
 */
public interface MinecraftSecretStore {
    public fun loadInstallation(key: PaperInstallationKey): PaperInstallationCredential?

    public fun saveInstallation(key: PaperInstallationKey, credential: PaperInstallationCredential)

    public fun removeInstallation(key: PaperInstallationKey)

    public fun loadPlayerGrant(key: PlayerGrantKey): PlayerGrantCredential?

    public fun savePlayerGrant(credential: PlayerGrantCredential)

    public fun removePlayerGrant(key: PlayerGrantKey)
}

public class SecretPersistenceUnavailableException : IllegalStateException(
    "No secure Minecraft credential store is configured",
)

/** Rejects all access until the platform explicitly chooses a credential store. */
public object FailClosedMinecraftSecretStore : MinecraftSecretStore {
    override fun loadInstallation(key: PaperInstallationKey): PaperInstallationCredential? = unavailable()

    override fun saveInstallation(key: PaperInstallationKey, credential: PaperInstallationCredential): Unit =
        unavailable()

    override fun removeInstallation(key: PaperInstallationKey): Unit = unavailable()

    override fun loadPlayerGrant(key: PlayerGrantKey): PlayerGrantCredential? = unavailable()

    override fun savePlayerGrant(credential: PlayerGrantCredential): Unit = unavailable()

    override fun removePlayerGrant(key: PlayerGrantKey): Unit = unavailable()

    private fun unavailable(): Nothing = throw SecretPersistenceUnavailableException()
}

/**
 * Bounded, thread-safe storage that keeps credentials only in this process.
 *
 * Expired grants are removed on reads and saves. Saving a new key at capacity throws
 * instead of evicting a usable credential. This store does not persist across restarts.
 */
public class EphemeralMinecraftSecretStore(
    private val clock: Clock = Clock.systemUTC(),
    private val maxInstallationCredentials: Int = 16,
    private val maxPlayerGrants: Int = 4_096,
) : MinecraftSecretStore {
    private val installations = ConcurrentHashMap<PaperInstallationKey, PaperInstallationCredential>()
    private val grants = ConcurrentHashMap<PlayerGrantKey, PlayerGrantCredential>()

    init {
        require(maxInstallationCredentials > 0 && maxPlayerGrants > 0) { "credential bounds must be positive" }
    }

    override fun loadInstallation(key: PaperInstallationKey): PaperInstallationCredential? = installations[key]

    override fun saveInstallation(key: PaperInstallationKey, credential: PaperInstallationCredential) {
        synchronized(installations) {
            require(installations.containsKey(key) || installations.size < maxInstallationCredentials) {
                "ephemeral installation credential capacity was reached"
            }
            installations[key] = credential
        }
    }

    override fun removeInstallation(key: PaperInstallationKey) {
        val installationId = installations.remove(key)?.installationId ?: return
        grants.entries.removeIf { it.key.installationId == installationId }
    }

    override fun loadPlayerGrant(key: PlayerGrantKey): PlayerGrantCredential? {
        val grant = grants[key] ?: return null
        if (!grant.isUsable(clock.instant())) {
            grants.remove(key, grant)
            return null
        }
        return grant
    }

    override fun savePlayerGrant(credential: PlayerGrantCredential) {
        synchronized(grants) {
            grants.entries.removeIf { !it.value.isUsable(clock.instant()) }
            require(grants.containsKey(credential.key) || grants.size < maxPlayerGrants) {
                "ephemeral player grant capacity was reached"
            }
            grants[credential.key] = credential
        }
    }

    override fun removePlayerGrant(key: PlayerGrantKey) {
        grants.remove(key)
    }
}

public class MissingMinecraftCredentialException(kind: String) : IllegalStateException(
    "No $kind credential is available",
)

public class ExpiredMinecraftGrantException : IllegalStateException("The Minecraft player grant has expired")

private fun String.isVisibleAscii(): Boolean = all { it.code in 0x21..0x7e }

private fun validPlayerToken(token: String, expectedGrantId: UUID): Boolean {
    if (token.length !in 32..512 || !token.isVisibleAscii()) {
        return false
    }
    val parts = token.split('_', limit = 3)
    return parts.size == 3 &&
        parts[0] == "sqpt" &&
        parseCompactUuid(parts[1]) == expectedGrantId &&
        parts[2].length in 32..256 && parts[2].all { it.isLetterOrDigit() || it == '_' || it == '-' }
}

private fun parseCompactUuid(value: String): UUID? {
    if (value.length != 32 || value.any { it.digitToIntOrNull(16) == null }) {
        return null
    }
    val canonical = "${value.substring(0, 8)}-${value.substring(8, 12)}-${value.substring(12, 16)}-" +
        "${value.substring(16, 20)}-${value.substring(20)}"
    return runCatching { UUID.fromString(canonical) }.getOrNull()
}
