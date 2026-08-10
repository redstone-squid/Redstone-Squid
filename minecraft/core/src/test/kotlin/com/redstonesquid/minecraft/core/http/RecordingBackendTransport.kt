package com.redstonesquid.minecraft.core.http

import java.util.concurrent.CompletableFuture

class RecordingBackendTransport(
    private val responder: (BackendRequest) -> BackendResponse,
) : BackendTransport {
    val requests: MutableList<BackendRequest> = mutableListOf()

    override fun execute(request: BackendRequest): CompletableFuture<BackendResponse> {
        requests += request
        return try {
            CompletableFuture.completedFuture(responder(request))
        } catch (error: Exception) {
            CompletableFuture.failedFuture(error)
        }
    }
}

fun jsonResponse(status: Int = 200, body: String): BackendResponse = BackendResponse(
    statusCode = status,
    headers = mapOf("cache-control" to listOf("no-store")),
    body = body,
)
