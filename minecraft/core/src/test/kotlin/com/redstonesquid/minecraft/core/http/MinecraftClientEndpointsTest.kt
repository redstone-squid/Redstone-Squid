package com.redstonesquid.minecraft.core.http

import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class MinecraftClientEndpointsTest {
    @Test
    fun `configuration requires explicit HTTPS API and approval endpoints`() {
        val endpoints = MinecraftClientEndpoints.parse(
            "https://api.example.test/service/v1",
            "https://www.example.test/minecraft/link",
        )

        assertEquals("https://api.example.test/service/v1/", endpoints.apiBaseUri.toString())
        assertEquals("https://www.example.test/minecraft/link", endpoints.approvalUri.toString())
        assertFailsWith<IllegalArgumentException> { MinecraftClientEndpoints.parse(null, endpoints.approvalUri.toString()) }
        assertFailsWith<IllegalArgumentException> {
            MinecraftClientEndpoints.parse("https://api.example.test/", endpoints.approvalUri.toString())
        }
        assertFailsWith<IllegalArgumentException> {
            MinecraftClientEndpoints.parse(endpoints.apiBaseUri.toString(), "http://www.example.test/link")
        }
    }
}
