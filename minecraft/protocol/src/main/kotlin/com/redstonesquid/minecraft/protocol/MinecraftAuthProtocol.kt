package com.redstonesquid.minecraft.protocol

import java.net.URI
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

public const val MAX_AUTH_RESPONSE_BYTES: Int = 64 * 1024

private val opaqueCodePattern = Regex("[A-Za-z0-9_-]{32,256}")
private val userCodePattern = Regex("[A-Za-z2-7a-z-]{8,32}")
private val pkcePattern = Regex("[A-Za-z0-9_-]{43}")
private val pkceVerifierPattern = Regex("[A-Za-z0-9._~-]{43,128}")

public enum class MinecraftOrigin(public val wireValue: String) {
    PAPER("paper"),
    FABRIC("fabric"),
}

@Serializable
public data class PaperChallengeCreateRequest(
    @SerialName("java_uuid")
    public val javaUuid: String,
) {
    init {
        requireUuid(javaUuid, "java_uuid")
    }
}

@Serializable
public data class FabricChallengeCreateRequest(
    @SerialName("java_uuid")
    public val javaUuid: String,
    @SerialName("pkce_s256_challenge")
    public val pkceS256Challenge: String,
) {
    init {
        requireUuid(javaUuid, "java_uuid")
        require(pkcePattern.matches(pkceS256Challenge)) { "pkce_s256_challenge has an invalid format" }
    }
}

@Serializable
public data class PaperChallengeExchangeRequest(
    @SerialName("device_code")
    public val deviceCode: String,
) {
    init {
        require(opaqueCodePattern.matches(deviceCode)) { "device_code has an invalid format" }
    }

    override fun toString(): String = "PaperChallengeExchangeRequest(deviceCode=<redacted>)"
}

@Serializable
public data class FabricChallengeExchangeRequest(
    @SerialName("device_code")
    public val deviceCode: String,
    @SerialName("pkce_verifier")
    public val pkceVerifier: String,
) {
    init {
        require(opaqueCodePattern.matches(deviceCode)) { "device_code has an invalid format" }
        require(pkceVerifierPattern.matches(pkceVerifier)) { "pkce_verifier has an invalid format" }
    }

    override fun toString(): String =
        "FabricChallengeExchangeRequest(deviceCode=<redacted>, pkceVerifier=<redacted>)"
}

@Serializable
public class ChallengeCreateResponse(
    public val id: String,
    @SerialName("device_code")
    private val deviceCode: String,
    @SerialName("user_code")
    public val userCode: String,
    @SerialName("expires_at")
    public val expiresAt: String,
    @SerialName("polling_interval_seconds")
    public val pollingIntervalSeconds: Int,
    @SerialName("verification_uri")
    public val verificationUri: String? = null,
    @SerialName("verification_uri_complete")
    public val verificationUriComplete: String? = null,
) {
    init {
        requireUuid(id, "challenge ID")
        require(opaqueCodePattern.matches(deviceCode)) { "device_code has an invalid format" }
        require(userCodePattern.matches(userCode)) { "user_code has an invalid format" }
        requireInstant(expiresAt, "expires_at")
        require(pollingIntervalSeconds in 1..300) { "polling_interval_seconds is outside the supported range" }
        verificationUri?.let { requirePublicHttpsUri(it, "verification_uri") }
        verificationUriComplete?.let { requirePublicHttpsUri(it, "verification_uri_complete") }
    }

    public fun <T> useDeviceCode(action: (String) -> T): T = action(deviceCode)

    override fun toString(): String =
        "ChallengeCreateResponse(id=$id, deviceCode=<redacted>, userCode=$userCode, " +
            "expiresAt=$expiresAt, pollingIntervalSeconds=$pollingIntervalSeconds, " +
            "verificationUri=$verificationUri, verificationUriComplete=$verificationUriComplete)"
}

/** A one-time player bearer response. The token is deliberately absent from [toString]. */
@Serializable
public class IssuedPlayerGrantResponse(
    @SerialName("grant_id")
    public val grantId: String,
    private val token: String,
    @SerialName("java_uuid")
    public val javaUuid: String,
    public val origin: String,
    @SerialName("installation_id")
    public val installationId: String? = null,
    @SerialName("expires_at")
    public val expiresAt: String,
) {
    init {
        requireUuid(grantId, "grant_id")
        require(validPlayerToken(token, UUID.fromString(grantId))) {
            "token has an invalid format"
        }
        requireUuid(javaUuid, "java_uuid")
        require(origin == MinecraftOrigin.PAPER.wireValue || origin == MinecraftOrigin.FABRIC.wireValue) {
            "origin is unsupported"
        }
        installationId?.let { requireUuid(it, "installation_id") }
        requireInstant(expiresAt, "expires_at")
    }

    public fun <T> useToken(action: (String) -> T): T = action(token)

    override fun toString(): String =
        "IssuedPlayerGrantResponse(grantId=$grantId, token=<redacted>, javaUuid=$javaUuid, " +
            "origin=$origin, installationId=$installationId, expiresAt=$expiresAt)"
}

@Serializable
public data class ProblemDetailResponse(
    public val title: String,
    public val status: Int,
    public val detail: String? = null,
    public val code: String? = null,
    public val resource: String? = null,
    public val context: Map<String, kotlinx.serialization.json.JsonElement>? = null,
    @SerialName("error_id")
    public val errorId: String? = null,
)

public object MinecraftAuthProtocolJson {
    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        isLenient = false
        allowSpecialFloatingPointValues = false
        allowTrailingComma = false
    }

    public fun encodePaperChallenge(request: PaperChallengeCreateRequest): String = json.encodeToString(request)

    public fun encodePaperExchange(request: PaperChallengeExchangeRequest): String = json.encodeToString(request)

    public fun encodeFabricChallenge(request: FabricChallengeCreateRequest): String = json.encodeToString(request)

    public fun encodeFabricExchange(request: FabricChallengeExchangeRequest): String = json.encodeToString(request)

    public fun decodeChallenge(document: String): ChallengeCreateResponse =
        json.decodeFromString<ChallengeCreateResponse>(document)

    public fun decodeGrant(document: String): IssuedPlayerGrantResponse =
        json.decodeFromString<IssuedPlayerGrantResponse>(document)

    public fun decodeProblem(document: String): ProblemDetailResponse = json.decodeFromString(document)
}

private fun requireUuid(value: String, name: String) {
    require(runCatching { UUID.fromString(value) }.isSuccess) { "$name must be a UUID" }
}

private fun requireInstant(value: String, name: String) {
    require(runCatching { Instant.parse(value) }.isSuccess) { "$name must be an RFC 3339 instant" }
}

private fun requirePublicHttpsUri(value: String, name: String) {
    val uri = runCatching { URI(value) }.getOrNull()
    require(
        uri != null && uri.scheme.equals("https", ignoreCase = true) && uri.host != null &&
            uri.rawUserInfo == null && uri.rawFragment == null,
    ) { "$name must be an absolute public HTTPS URI" }
}

private fun String.isVisibleAscii(): Boolean = all { it.code in 0x21..0x7e }

private fun validPlayerToken(token: String, expectedGrantId: UUID): Boolean {
    if (token.length !in 32..512 || !token.isVisibleAscii()) {
        return false
    }
    val parts = token.split('_', limit = 3)
    return parts.size == 3 &&
        parts[0] == "sqpt" &&
        parseCompactUuid(parts[1]) == expectedGrantId &&
        opaqueCodePattern.matches(parts[2])
}

private fun parseCompactUuid(value: String): UUID? {
    if (value.length != 32 || value.any { it.digitToIntOrNull(16) == null }) {
        return null
    }
    val canonical = "${value.substring(0, 8)}-${value.substring(8, 12)}-${value.substring(12, 16)}-" +
        "${value.substring(16, 20)}-${value.substring(20)}"
    return runCatching { UUID.fromString(canonical) }.getOrNull()
}
