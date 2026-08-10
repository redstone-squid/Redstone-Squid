package com.redstonesquid.minecraft.core.http

import com.redstonesquid.minecraft.protocol.MinecraftAuthProtocolJson
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.util.concurrent.CompletableFuture

public class BackendApiException(
    public val statusCode: Int,
    public val code: String?,
    public val minecraftAuthCode: String?,
    public val retryAfterSeconds: Long?,
) : RuntimeException(
    buildString {
        append("Backend rejected the request with HTTP ")
        append(statusCode)
        code?.let { append(" (").append(it).append(')') }
    },
)

internal fun <T> BackendTransport.executeJson(
    request: BackendRequest,
    responseName: String,
    decode: (String) -> T,
): CompletableFuture<T> = execute(request).thenApply { response ->
    if (response.statusCode !in 200..299) {
        throw apiException(response)
    }
    try {
        decode(response.body)
    } catch (_: Exception) {
        throw BackendProtocolException("Backend returned an invalid $responseName")
    }
}

internal fun BackendTransport.executeEmpty(
    request: BackendRequest,
    expectedStatus: Int,
): CompletableFuture<Unit> = execute(request).thenApply { response ->
    if (response.statusCode != expectedStatus) {
        throw apiException(response)
    }
}

private fun apiException(response: BackendResponse): BackendApiException {
    val problem = try {
        MinecraftAuthProtocolJson.decodeProblem(response.body)
    } catch (_: Exception) {
        null
    }
    val authCode = (problem?.context?.get("minecraft_auth_code") as? JsonPrimitive)?.contentOrNull
    val retryAfter = response.header("Retry-After").singleOrNull()?.toLongOrNull()?.takeIf { it >= 0 }
    return BackendApiException(
        statusCode = response.statusCode,
        code = problem?.code,
        minecraftAuthCode = authCode,
        retryAfterSeconds = retryAfter,
    )
}
