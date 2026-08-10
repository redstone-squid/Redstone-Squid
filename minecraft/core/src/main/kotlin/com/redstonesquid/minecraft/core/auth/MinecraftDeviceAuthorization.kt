package com.redstonesquid.minecraft.core.auth

import com.redstonesquid.minecraft.core.http.BackendHttpMethod
import com.redstonesquid.minecraft.core.http.BackendRequest
import com.redstonesquid.minecraft.core.http.BackendTransport
import com.redstonesquid.minecraft.core.http.executeJson
import com.redstonesquid.minecraft.protocol.ChallengeCreateResponse
import com.redstonesquid.minecraft.protocol.FabricChallengeCreateRequest
import com.redstonesquid.minecraft.protocol.FabricChallengeExchangeRequest
import com.redstonesquid.minecraft.protocol.IssuedPlayerGrantResponse
import com.redstonesquid.minecraft.protocol.MAX_AUTH_RESPONSE_BYTES
import com.redstonesquid.minecraft.protocol.MinecraftAuthApiPaths
import com.redstonesquid.minecraft.protocol.MinecraftAuthProtocolJson
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import com.redstonesquid.minecraft.protocol.PaperChallengeCreateRequest
import com.redstonesquid.minecraft.protocol.PaperChallengeExchangeRequest
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Clock
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture

public sealed interface PendingPlayerAuthorization {
    public val challengeId: UUID
    public val javaUuid: UUID
    public val userCode: String
    public val expiresAt: Instant
    public val pollingIntervalSeconds: Int
}

public class PendingPaperAuthorization internal constructor(
    override val challengeId: UUID,
    override val javaUuid: UUID,
    override val userCode: String,
    override val expiresAt: Instant,
    override val pollingIntervalSeconds: Int,
    public val installationKey: PaperInstallationKey,
    internal val installationId: UUID,
    private val deviceCode: String,
) : PendingPlayerAuthorization {
    internal fun <T> useDeviceCode(action: (String) -> T): T = action(deviceCode)

    override fun toString(): String =
        "PendingPaperAuthorization(challengeId=$challengeId, javaUuid=$javaUuid, userCode=$userCode, " +
            "expiresAt=$expiresAt, pollingIntervalSeconds=$pollingIntervalSeconds, " +
            "installationKey=$installationKey, deviceCode=<redacted>)"
}

public class PendingFabricAuthorization internal constructor(
    override val challengeId: UUID,
    override val javaUuid: UUID,
    override val userCode: String,
    override val expiresAt: Instant,
    override val pollingIntervalSeconds: Int,
    private val deviceCode: String,
    private val pkceVerifier: String,
) : PendingPlayerAuthorization {
    internal fun <T> useSecrets(action: (deviceCode: String, pkceVerifier: String) -> T): T =
        action(deviceCode, pkceVerifier)

    override fun toString(): String =
        "PendingFabricAuthorization(challengeId=$challengeId, javaUuid=$javaUuid, userCode=$userCode, " +
            "expiresAt=$expiresAt, pollingIntervalSeconds=$pollingIntervalSeconds, " +
            "deviceCode=<redacted>, pkceVerifier=<redacted>)"
}

public data class AuthorizedPlayer(
    public val grantId: UUID,
    public val key: PlayerGrantKey,
    public val expiresAt: Instant,
)

/** Starts and exchanges the server's Paper and Fabric device authorization flows. */
public class MinecraftDeviceAuthorizationClient(
    private val transport: BackendTransport,
    private val secretStore: MinecraftSecretStore = FailClosedMinecraftSecretStore,
    private val clock: Clock = Clock.systemUTC(),
    private val secureRandom: SecureRandom = SecureRandom(),
) {
    public fun beginPaper(
        javaUuid: UUID,
        installationKey: PaperInstallationKey,
    ): CompletableFuture<PendingPaperAuthorization> {
        val installation = secretStore.loadInstallation(installationKey)
            ?: throw MissingMinecraftCredentialException("Paper installation")
        val body = MinecraftAuthProtocolJson.encodePaperChallenge(PaperChallengeCreateRequest(javaUuid.toString()))
        return transport.executeJson(
            authRequest(MinecraftAuthApiPaths.PAPER_CHALLENGES, body, installation.headers()),
            "Paper challenge response",
            MinecraftAuthProtocolJson::decodeChallenge,
        ).thenApply { response ->
            pendingPaper(response, javaUuid, installationKey, installation.installationId)
        }
    }

    public fun exchangePaper(pending: PendingPaperAuthorization): CompletableFuture<AuthorizedPlayer> {
        requirePending(pending)
        val installation = secretStore.loadInstallation(pending.installationKey)
            ?: throw MissingMinecraftCredentialException("Paper installation")
        require(installation.installationId == pending.installationId) {
            "Paper installation changed while authorization was pending"
        }
        val body = pending.useDeviceCode { deviceCode ->
            MinecraftAuthProtocolJson.encodePaperExchange(PaperChallengeExchangeRequest(deviceCode))
        }
        return transport.executeJson(
            authRequest(MinecraftAuthApiPaths.PAPER_EXCHANGE, body, installation.headers()),
            "Paper player grant response",
            MinecraftAuthProtocolJson::decodeGrant,
        ).thenApply { response ->
            storeGrant(
                response,
                expectedJavaUuid = pending.javaUuid,
                expectedOrigin = MinecraftOrigin.PAPER,
                expectedInstallationId = installation.installationId,
            )
        }
    }

    public fun beginFabric(javaUuid: UUID): CompletableFuture<PendingFabricAuthorization> {
        val pkce = PkcePair.generate(secureRandom)
        val body = MinecraftAuthProtocolJson.encodeFabricChallenge(
            FabricChallengeCreateRequest(javaUuid.toString(), pkce.challenge),
        )
        return transport.executeJson(
            authRequest(MinecraftAuthApiPaths.FABRIC_CHALLENGES, body),
            "Fabric challenge response",
            MinecraftAuthProtocolJson::decodeChallenge,
        ).thenApply { response -> pendingFabric(response, javaUuid, pkce.verifier) }
    }

    public fun exchangeFabric(pending: PendingFabricAuthorization): CompletableFuture<AuthorizedPlayer> {
        requirePending(pending)
        val body = pending.useSecrets { deviceCode, verifier ->
            MinecraftAuthProtocolJson.encodeFabricExchange(FabricChallengeExchangeRequest(deviceCode, verifier))
        }
        return transport.executeJson(
            authRequest(MinecraftAuthApiPaths.FABRIC_EXCHANGE, body),
            "Fabric player grant response",
            MinecraftAuthProtocolJson::decodeGrant,
        ).thenApply { response ->
            storeGrant(
                response,
                expectedJavaUuid = pending.javaUuid,
                expectedOrigin = MinecraftOrigin.FABRIC,
                expectedInstallationId = null,
            )
        }
    }

    private fun pendingPaper(
        response: ChallengeCreateResponse,
        javaUuid: UUID,
        installationKey: PaperInstallationKey,
        installationId: UUID,
    ): PendingPaperAuthorization = response.useDeviceCode { deviceCode ->
        PendingPaperAuthorization(
            challengeId = UUID.fromString(response.id),
            javaUuid = javaUuid,
            userCode = response.userCode,
            expiresAt = Instant.parse(response.expiresAt),
            pollingIntervalSeconds = response.pollingIntervalSeconds,
            installationKey = installationKey,
            installationId = installationId,
            deviceCode = deviceCode,
        ).also(::requirePending)
    }

    private fun pendingFabric(
        response: ChallengeCreateResponse,
        javaUuid: UUID,
        verifier: String,
    ): PendingFabricAuthorization = response.useDeviceCode { deviceCode ->
        PendingFabricAuthorization(
            challengeId = UUID.fromString(response.id),
            javaUuid = javaUuid,
            userCode = response.userCode,
            expiresAt = Instant.parse(response.expiresAt),
            pollingIntervalSeconds = response.pollingIntervalSeconds,
            deviceCode = deviceCode,
            pkceVerifier = verifier,
        ).also(::requirePending)
    }

    private fun requirePending(pending: PendingPlayerAuthorization) {
        require(pending.expiresAt.isAfter(clock.instant())) { "Minecraft authorization challenge has expired" }
    }

    private fun storeGrant(
        response: IssuedPlayerGrantResponse,
        expectedJavaUuid: UUID,
        expectedOrigin: MinecraftOrigin,
        expectedInstallationId: UUID?,
    ): AuthorizedPlayer {
        val actualJavaUuid = UUID.fromString(response.javaUuid)
        val actualInstallationId = response.installationId?.let(UUID::fromString)
        if (
            actualJavaUuid != expectedJavaUuid ||
            response.origin != expectedOrigin.wireValue ||
            actualInstallationId != expectedInstallationId
        ) {
            throw IllegalStateException("Backend player grant binding did not match the authorization request")
        }
        val key = PlayerGrantKey(expectedJavaUuid, expectedOrigin, expectedInstallationId)
        val expiresAt = Instant.parse(response.expiresAt)
        require(expiresAt.isAfter(clock.instant())) { "Backend issued an already-expired player grant" }
        val credential = response.useToken { token ->
            PlayerGrantCredential(
                grantId = UUID.fromString(response.grantId),
                key = key,
                token = token,
                expiresAt = expiresAt,
            )
        }
        secretStore.savePlayerGrant(credential)
        return AuthorizedPlayer(credential.grantId, key, expiresAt)
    }

    private fun authRequest(path: String, body: String, headers: Map<String, String> = emptyMap()): BackendRequest =
        BackendRequest(
            method = BackendHttpMethod.POST,
            pathAndQuery = path,
            body = body,
            headers = headers,
            maxResponseBytes = MAX_AUTH_RESPONSE_BYTES,
            requireNoStoreResponse = true,
        )
}

private class PkcePair(val verifier: String, val challenge: String) {
    override fun toString(): String = "PkcePair(verifier=<redacted>, challenge=$challenge)"

    companion object {
        fun generate(random: SecureRandom): PkcePair {
            val entropy = ByteArray(32)
            random.nextBytes(entropy)
            val verifier = Base64.getUrlEncoder().withoutPadding().encodeToString(entropy)
            val challenge = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.encodeToByteArray()))
            return PkcePair(verifier, challenge)
        }
    }
}
