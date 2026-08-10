package com.redstonesquid.minecraft.core.http

import java.io.InputStream
import java.net.InetAddress
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.HttpTimeoutException
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.Locale
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

public const val MAX_BACKEND_REQUEST_BYTES: Int = 256 * 1024
public const val MAX_BACKEND_RESPONSE_BYTES: Int = 1024 * 1024

public enum class BackendHttpMethod {
    GET,
    POST,
    DELETE,
}

/**
 * One bounded backend operation. Its string representation intentionally omits
 * the body and headers because both can carry device or bearer credentials.
 */
public class BackendRequest(
    public val method: BackendHttpMethod,
    public val pathAndQuery: String,
    public val body: String? = null,
    headers: Map<String, String> = emptyMap(),
    public val maxResponseBytes: Int,
    public val requireNoStoreResponse: Boolean = false,
) {
    public val headers: Map<String, String> = java.util.Map.copyOf(headers)

    init {
        require(pathAndQuery.startsWith('/')) { "backend path must start with /" }
        require(!pathAndQuery.contains("://") && !pathAndQuery.contains('#')) { "backend path must be relative" }
        require(pathAndQuery.split('?', limit = 2).first().split('/').none { it == ".." || it == "." }) {
            "backend path must not contain traversal segments"
        }
        require(pathAndQuery.none { it == '\r' || it == '\n' }) { "backend path contains a line break" }
        require(body == null || body.encodeToByteArray().size <= MAX_BACKEND_REQUEST_BYTES) {
            "backend request body exceeds $MAX_BACKEND_REQUEST_BYTES bytes"
        }
        require(maxResponseBytes in 1..MAX_BACKEND_RESPONSE_BYTES) { "invalid backend response byte budget" }
        require(headers.keys.map { it.lowercase(Locale.ROOT) }.distinct().size == headers.size) {
            "backend header names must be unique ignoring case"
        }
        require(headers.all { (name, value) -> validHeaderName(name) && validHeaderValue(value) }) {
            "backend request contains an invalid header"
        }
        require(body != null || method != BackendHttpMethod.POST) { "POST requests require a JSON body" }
    }

    override fun toString(): String =
        "BackendRequest(method=$method, pathAndQuery=$pathAndQuery, body=<redacted>, headers=<redacted>)"
}

public class BackendResponse(
    public val statusCode: Int,
    headers: Map<String, List<String>>,
    public val body: String,
) {
    public val headers: Map<String, List<String>> = java.util.Map.copyOf(
        headers.mapValues { (_, values) -> java.util.List.copyOf(values) },
    )

    public fun header(name: String): List<String> =
        headers.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value.orEmpty()

    override fun toString(): String = "BackendResponse(statusCode=$statusCode, headers=<redacted>, body=<redacted>)"
}

public fun interface BackendTransport {
    public fun execute(request: BackendRequest): CompletableFuture<BackendResponse>
}

public open class BackendTransportException(message: String) : RuntimeException(message)

public class BackendProtocolException(message: String) : BackendTransportException(message)

public class BackendResponseTooLargeException(limit: Int) :
    BackendTransportException("Backend response exceeded its $limit-byte budget")

/** A redirect-free JDK HTTP transport with strict URI, timeout, and body bounds. */
public class BoundedJdkHttpTransport(
    apiBaseUri: URI,
    connectTimeout: Duration = Duration.ofSeconds(5),
    private val requestTimeout: Duration = Duration.ofSeconds(15),
    allowInsecureLoopback: Boolean = false,
) : BackendTransport {
    private val apiBaseUri: URI = normalizeBaseUri(apiBaseUri, allowInsecureLoopback)
    private val client: HttpClient

    init {
        require(connectTimeout in Duration.ofMillis(100)..Duration.ofSeconds(60)) {
            "connect timeout must be between 100 milliseconds and 60 seconds"
        }
        require(requestTimeout in Duration.ofMillis(100)..Duration.ofSeconds(60)) {
            "request timeout must be between 100 milliseconds and 60 seconds"
        }
        client = HttpClient.newBuilder()
            .connectTimeout(connectTimeout)
            .followRedirects(HttpClient.Redirect.NEVER)
            .version(HttpClient.Version.HTTP_2)
            .build()
    }

    override fun execute(request: BackendRequest): CompletableFuture<BackendResponse> {
        val outgoing = buildRequest(request)
        val upstream = client.sendAsync(outgoing, HttpResponse.BodyHandlers.ofInputStream())
        val result = CompletableFuture<BackendResponse>()
        val activeBody = AtomicReference<InputStream?>()
        upstream.whenComplete { response, failure ->
            if (failure != null) {
                result.completeExceptionally(transportFailure(failure))
                return@whenComplete
            }
            activeBody.set(response.body())
            if (result.isDone) {
                activeBody.getAndSet(null)?.close()
                return@whenComplete
            }
            try {
                result.complete(readResponse(response, request))
            } catch (error: BackendTransportException) {
                result.completeExceptionally(error)
            } catch (_: Exception) {
                result.completeExceptionally(BackendTransportException("Backend response could not be read"))
            } finally {
                activeBody.getAndSet(null)?.close()
            }
        }
        CompletableFuture.delayedExecutor(requestTimeout.toMillis(), TimeUnit.MILLISECONDS).execute {
            if (result.completeExceptionally(BackendTransportException("Backend request timed out"))) {
                upstream.cancel(true)
                activeBody.getAndSet(null)?.close()
            }
        }
        result.whenComplete { _, _ ->
            if (result.isCancelled) {
                upstream.cancel(true)
                activeBody.getAndSet(null)?.close()
            }
        }
        return result
    }

    private fun buildRequest(request: BackendRequest): HttpRequest {
        val resolved = apiBaseUri.resolve(request.pathAndQuery.removePrefix("/"))
        if (!sameAuthority(apiBaseUri, resolved)) {
            throw BackendTransportException("Backend request path escaped the configured API origin")
        }
        val builder = HttpRequest.newBuilder(resolved)
            .timeout(requestTimeout)
            .header("Accept", "application/json, application/problem+json")
            .header("User-Agent", "redstone-squid-minecraft/0.1")
        if (request.requireNoStoreResponse) {
            builder.header("Cache-Control", "no-store")
            builder.header("Pragma", "no-cache")
        }
        request.headers.forEach { (name, value) -> builder.header(name, value) }
        val bodyPublisher = request.body?.let {
            builder.header("Content-Type", "application/json")
            HttpRequest.BodyPublishers.ofString(it, StandardCharsets.UTF_8)
        } ?: HttpRequest.BodyPublishers.noBody()
        return when (request.method) {
            BackendHttpMethod.GET -> builder.GET().build()
            BackendHttpMethod.POST -> builder.POST(bodyPublisher).build()
            BackendHttpMethod.DELETE -> builder.method("DELETE", bodyPublisher).build()
        }
    }

    private fun readResponse(
        response: HttpResponse<InputStream>,
        request: BackendRequest,
    ): BackendResponse {
        val declaredLength = response.headers().firstValueAsLong("Content-Length")
        if (declaredLength.isPresent && declaredLength.asLong > request.maxResponseBytes) {
            response.body().close()
            throw BackendResponseTooLargeException(request.maxResponseBytes)
        }
        val bytes = response.body().use { body -> body.readNBytes(request.maxResponseBytes + 1) }
        if (bytes.size > request.maxResponseBytes) {
            throw BackendResponseTooLargeException(request.maxResponseBytes)
        }
        if (request.requireNoStoreResponse && response.statusCode() in 200..299) {
            val cacheControl = response.headers().allValues("Cache-Control")
                .flatMap { it.split(',') }
                .map { it.trim().lowercase(Locale.ROOT) }
            if ("no-store" !in cacheControl) {
                throw BackendProtocolException("Sensitive backend response did not prohibit storage")
            }
        }
        return BackendResponse(
            statusCode = response.statusCode(),
            headers = response.headers().map(),
            body = decodeUtf8(bytes),
        )
    }

    private companion object {
        fun normalizeBaseUri(input: URI, allowInsecureLoopback: Boolean): URI {
            require(input.isAbsolute && input.host != null) { "API base URI must be absolute and include a host" }
            require(input.rawUserInfo == null && input.rawQuery == null && input.rawFragment == null) {
                "API base URI must not contain credentials, a query, or a fragment"
            }
            val scheme = input.scheme.lowercase(Locale.ROOT)
            val loopbackHttp = scheme == "http" && allowInsecureLoopback && isLoopback(input.host)
            require(scheme == "https" || loopbackHttp) { "API base URI must use HTTPS" }
            val path = if (input.path.endsWith('/')) input.path else "${input.path}/"
            return URI(scheme, null, input.host, input.port, path, null, null)
        }

        fun isLoopback(host: String): Boolean =
            host.equals("localhost", ignoreCase = true) || runCatching { InetAddress.getByName(host).isLoopbackAddress }
                .getOrDefault(false)

        fun sameAuthority(base: URI, resolved: URI): Boolean =
            base.scheme.equals(resolved.scheme, ignoreCase = true) &&
                base.host.equals(resolved.host, ignoreCase = true) &&
                base.port == resolved.port

        fun decodeUtf8(bytes: ByteArray): String = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes))
                .toString()
        } catch (_: Exception) {
            throw BackendProtocolException("Backend response was not valid UTF-8")
        }
    }
}

private fun validHeaderName(value: String): Boolean =
    value.isNotEmpty() && value.all { it.isLetterOrDigit() || it in "!#$%&'*+-.^_`|~" }

private fun validHeaderValue(value: String): Boolean =
    value.none { it == '\r' || it == '\n' || it.code == 0 || it.code == 0x7f }

private fun transportFailure(failure: Throwable): BackendTransportException {
    var current: Throwable? = failure
    repeat(8) {
        if (current is HttpTimeoutException) {
            return BackendTransportException("Backend request timed out")
        }
        current = current?.cause
    }
    return BackendTransportException("Backend request failed")
}
