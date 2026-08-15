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

    @Test
    fun `draft fields and values are suggested from what the client already holds`() {
        val recorded = mutableListOf<Pair<SuggestionSlot, String?>>()
        val suggestions = SquidSuggestions<TestSource> { _, slot, field ->
            recorded += slot to field
            when (slot) {
                SuggestionSlot.SET_FIELD -> listOf(SquidSuggestion("description"), SquidSuggestion("door_type"))
                SuggestionSlot.SET_VALUE -> listOf(SquidSuggestion("flush", "Flush"))
                SuggestionSlot.UNSET_FIELD -> listOf(SquidSuggestion("description"))
                SuggestionSlot.SUBMIT_TARGET -> listOf(SquidSuggestion("123e4567-e89b-42d3-a456-426614174000"))
            }
        }
        val dispatcher = dispatcher(suggestions) { _, _ -> 1 }
        val source = TestSource(setOf("redstonesquid.submit"))

        assertEquals(
            listOf("description", "door_type"),
            completions(dispatcher, source, "squid set "),
        )
        assertEquals(listOf("flush"), completions(dispatcher, source, "squid set door_type "))
        assertEquals(listOf("description"), completions(dispatcher, source, "squid unset "))
        assertEquals(
            listOf("123e4567-e89b-42d3-a456-426614174000"),
            completions(dispatcher, source, "squid submit "),
        )
        assertTrue(SuggestionSlot.SET_VALUE to "door_type" in recorded)
    }

    @Test
    fun `suggestions are filtered by what has already been typed`() {
        val dispatcher = dispatcher(
            SquidSuggestions { _, _, _ ->
                listOf(SquidSuggestion("description"), SquidSuggestion("door_type"))
            },
        ) { _, _ -> 1 }
        val source = TestSource(setOf("redstonesquid.submit"))

        assertEquals(listOf("door_type"), completions(dispatcher, source, "squid set door"))
        assertEquals(listOf("description", "door_type"), completions(dispatcher, source, "squid set D"))
    }

    @Test
    fun `a failing suggestion source completes to nothing instead of erroring at the player`() {
        val dispatcher = dispatcher(
            SquidSuggestions { _, _, _ -> error("session state is unavailable") },
        ) { _, _ -> 1 }

        assertEquals(emptyList(), completions(dispatcher, TestSource(setOf("redstonesquid.submit")), "squid set "))
    }

    @Test
    fun `commands without suggestions still register and run`() {
        val dispatcher = dispatcher { _, _ -> 1 }
        val source = TestSource(setOf("redstonesquid.submit"))

        assertEquals(emptyList(), completions(dispatcher, source, "squid set "))
        assertEquals(1, dispatcher.execute("squid set description compact", source))
    }

    private fun dispatcher(
        suggestions: SquidSuggestions<TestSource> = noSuggestions(),
        actions: CommandActions<TestSource>,
    ): CommandDispatcher<TestSource> =
        CommandDispatcher<TestSource>().also { dispatcher ->
            dispatcher.register(
                SquidCommandTree.build(
                    access = CommandAccess { source, action -> action.permission in source.permissions },
                    actions = actions,
                    suggestions = suggestions,
                ),
            )
        }

    private fun completions(
        dispatcher: CommandDispatcher<TestSource>,
        source: TestSource,
        input: String,
    ): List<String> = dispatcher
        .getCompletionSuggestions(dispatcher.parse(input, source))
        .join()
        .list
        .map { it.text }

    private data class TestSource(val permissions: Set<String>)
}
