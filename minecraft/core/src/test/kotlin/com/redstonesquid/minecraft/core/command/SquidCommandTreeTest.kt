package com.redstonesquid.minecraft.core.command

import com.mojang.brigadier.CommandDispatcher
import com.mojang.brigadier.arguments.StringArgumentType
import com.mojang.brigadier.exceptions.CommandSyntaxException
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class SquidCommandTreeTest {
    @Test
    fun `shared Brigadier tree dispatches typed actions`() {
        var executed: SquidCommandAction? = null
        val dispatcher = dispatcher { action, _ ->
            executed = action
            7
        }
        val source = TestSource(setOf("redstonesquid.submit"))

        assertEquals(7, dispatcher.execute("squid submit", source))
        assertEquals(SquidCommandAction.SUBMIT, executed)

        assertEquals(7, dispatcher.execute("squid submit door", source))
        assertEquals(SquidCommandAction.SUBMIT, executed)
        assertEquals(7, dispatcher.execute("squid set description compact and fast", source))
        assertEquals(SquidCommandAction.SET, executed)
    }

    @Test
    fun `draft arguments remain native Brigadier values`() {
        var target: String? = null
        var field: String? = null
        var value: String? = null
        val dispatcher = dispatcher { action, context ->
            when (action) {
                SquidCommandAction.SUBMIT -> target = StringArgumentType.getString(context, "target")
                SquidCommandAction.SET -> {
                    field = StringArgumentType.getString(context, "field")
                    value = StringArgumentType.getString(context, "value")
                }
                else -> Unit
            }
            1
        }
        val source = TestSource(setOf("redstonesquid.submit"))

        dispatcher.execute("squid submit door", source)
        dispatcher.execute("squid set description compact and fast", source)

        assertEquals("door", target)
        assertEquals("description", field)
        assertEquals("compact and fast", value)

        dispatcher.execute("squid submit 123e4567-e89b-42d3-a456-426614174000", source)
        assertEquals("123e4567-e89b-42d3-a456-426614174000", target)
    }

    @Test
    fun `native requirements reject actions without platform permission`() {
        val dispatcher = dispatcher { _, _ -> 1 }
        val source = TestSource(setOf("redstonesquid.use"))
        val parse = dispatcher.parse("squid ", source)
        val suggestions = dispatcher.getCompletionSuggestions(parse).join().list.map { it.text }.toSet()

        assertTrue("status" in suggestions)
        assertTrue("link" in suggestions)
        assertEquals(1, dispatcher.execute("squid status", source))
        assertFailsWith<CommandSyntaxException> { dispatcher.execute("squid submit", source) }
        assertFailsWith<CommandSyntaxException> { dispatcher.execute("squid server rotate", source) }
    }

    @Test
    fun `server commands remain in a permission-gated subtree`() {
        val dispatcher = dispatcher { _, _ -> 1 }
        val source = TestSource(setOf("redstonesquid.server.manage"))
        val rootSuggestions = dispatcher.getCompletionSuggestions(dispatcher.parse("squid ", source)).join().list
            .map { it.text }
        val serverSuggestions = dispatcher
            .getCompletionSuggestions(dispatcher.parse("squid server ", source))
            .join()
            .list
            .map { it.text }

        assertTrue("server" in rootSuggestions)
        assertEquals(setOf("claim", "status", "rotate", "revoke"), serverSuggestions.toSet())
    }

    private fun dispatcher(actions: CommandActions<TestSource>): CommandDispatcher<TestSource> =
        CommandDispatcher<TestSource>().also { dispatcher ->
            dispatcher.register(
                SquidCommandTree.build(
                    access = CommandAccess { source, action -> action.permission in source.permissions },
                    actions = actions,
                ),
            )
        }

    private data class TestSource(val permissions: Set<String>)
}
