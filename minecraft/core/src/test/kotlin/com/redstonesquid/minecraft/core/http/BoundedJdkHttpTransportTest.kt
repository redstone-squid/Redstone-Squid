package com.redstonesquid.minecraft.core.http

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.net.URI
import java.time.Duration
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class BoundedJdkHttpTransportTest {
    private var server: HttpServer? = null

    @AfterEach
    fun stopServer() {
        server?.stop(0)
    }

    @Test
    fun `transport rejects plaintext remote endpoints`() {
        assertFailsWith<IllegalArgumentException> {
            BoundedJdkHttpTransport(URI("http://example.com/v1/"))
        }
    }

    @Test
    fun `transport sends bounded JSON without following redirects`() {
        val local = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).also { server = it }
        var receivedAuthorization: String? = null
        local.createContext("/v1/ok") { exchange ->
            receivedAuthorization = exchange.requestHeaders.getFirst("Authorization")
            exchange.responseHeaders.add("Content-Type", "application/json")
            exchange.responseHeaders.add("Cache-Control", "private, no-store")
            val body = "{\"ok\":true}".encodeToByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        local.createContext("/v1/redirect") { exchange ->
            exchange.responseHeaders.add("Location", "/v1/ok")
            exchange.sendResponseHeaders(302, -1)
            exchange.close()
        }
        local.start()
        val transport = localTransport(local)
        val secret = "Bearer sqpt_secret-never-print"
        val request = BackendRequest(
            method = BackendHttpMethod.POST,
            pathAndQuery = "/ok",
            body = "{}",
            headers = mapOf("Authorization" to secret),
            maxResponseBytes = 1024,
            requireNoStoreResponse = true,
        )

        val response = transport.execute(request).join()
        val redirect = transport.execute(
            BackendRequest(BackendHttpMethod.GET, "/redirect", maxResponseBytes = 1024),
        ).join()

        assertEquals(200, response.statusCode)
        assertEquals(secret, receivedAuthorization)
        assertEquals(302, redirect.statusCode)
        assertFalse(secret in request.toString())
    }

    @Test
    fun `transport enforces response budget and no-store response policy`() {
        val local = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).also { server = it }
        local.createContext("/v1/large") { exchange ->
            val body = "x".repeat(64).encodeToByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        local.createContext("/v1/cacheable") { exchange ->
            val body = "{}".encodeToByteArray()
            exchange.sendResponseHeaders(200, body.size.toLong())
            exchange.responseBody.use { it.write(body) }
        }
        local.start()
        val transport = localTransport(local)

        val tooLarge = assertFailsWith<java.util.concurrent.CompletionException> {
            transport.execute(
                BackendRequest(BackendHttpMethod.GET, "/large", maxResponseBytes = 16),
            ).join()
        }
        val cacheable = assertFailsWith<java.util.concurrent.CompletionException> {
            transport.execute(
                BackendRequest(
                    BackendHttpMethod.GET,
                    "/cacheable",
                    maxResponseBytes = 16,
                    requireNoStoreResponse = true,
                ),
            ).join()
        }

        assertTrue(tooLarge.cause is BackendResponseTooLargeException)
        assertTrue(cacheable.cause is BackendProtocolException)
    }

    @Test
    fun `transport timeout also bounds a response body that stalls after headers`() {
        val local = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).also { server = it }
        local.createContext("/v1/stalled") { exchange ->
            exchange.sendResponseHeaders(200, 0)
            exchange.responseBody.use { body ->
                body.write('x'.code)
                body.flush()
                Thread.sleep(3_000)
            }
        }
        local.start()
        val transport = BoundedJdkHttpTransport(
            apiBaseUri = URI("http://127.0.0.1:${local.address.port}/v1/"),
            connectTimeout = Duration.ofSeconds(2),
            requestTimeout = Duration.ofMillis(300),
            allowInsecureLoopback = true,
        )

        val startedAt = System.nanoTime()
        val failure = assertFailsWith<java.util.concurrent.CompletionException> {
            transport.execute(
                BackendRequest(BackendHttpMethod.GET, "/stalled", maxResponseBytes = 16),
            ).join()
        }
        val elapsedMillis = Duration.ofNanos(System.nanoTime() - startedAt).toMillis()

        assertTrue(failure.cause is BackendTransportException)
        assertEquals("Backend request timed out", failure.cause?.message)
        assertTrue(elapsedMillis < 2_000, "stalled response exceeded the total timeout: ${elapsedMillis}ms")
    }

    private fun localTransport(local: HttpServer): BoundedJdkHttpTransport = BoundedJdkHttpTransport(
        apiBaseUri = URI("http://127.0.0.1:${local.address.port}/v1/"),
        connectTimeout = Duration.ofSeconds(2),
        requestTimeout = Duration.ofSeconds(2),
        allowInsecureLoopback = true,
    )
}
