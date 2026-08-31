package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.protocol.ChoiceOption
import com.redstonesquid.minecraft.protocol.FieldConstraints
import com.redstonesquid.minecraft.protocol.FormField
import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.jupiter.api.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class DraftFieldValueParserTest {
    @Test
    fun `parser handles bounded typed manifest values`() {
        assertEquals(JsonPrimitive(30), DraftFieldValueParser.parse(field("game_ticks", "duration"), "1.5s"))
        assertEquals(JsonPrimitive(true), DraftFieldValueParser.parse(field("boolean", "boolean"), "yes"))
        assertEquals(
            JsonArray(listOf(JsonPrimitive("alice"), JsonPrimitive("bob"))),
            DraftFieldValueParser.parse(field("string_list", "text"), "alice, bob"),
        )
    }

    @Test
    fun `parser enforces inline and dynamic choices`() {
        val choice = field("string", "choice", listOf(ChoiceOption("door", "Door")))
        val dynamic = field("string_list", "multi_choice")

        assertEquals(JsonPrimitive("door"), DraftFieldValueParser.parse(choice, "door"))
        assertFailsWith<IllegalArgumentException> { DraftFieldValueParser.parse(choice, "unknown") }
        assertEquals(
            JsonArray(listOf(JsonPrimitive("compact"))),
            DraftFieldValueParser.parse(dynamic, "compact", listOf(ChoiceOption("compact", "Compact"))),
        )
    }

    @Test
    fun `parser rejects invalid ranges and fractional ticks`() {
        val number = FormField(
            id = "width",
            label = "Width",
            control = "number",
            valueKind = "integer",
            constraints = FieldConstraints(minimum = 1.0, maximum = 512.0),
            origins = listOf("paper"),
        )

        assertFailsWith<IllegalArgumentException> { DraftFieldValueParser.parse(number, "0") }
        assertFailsWith<IllegalArgumentException> {
            DraftFieldValueParser.parse(field("game_ticks", "duration"), "0.01s")
        }
    }

    @Test
    fun `parser matches the shared duration fixture`() {
        // Gradle runs tests with the module directory as the working directory.
        val fixture = Json
            .parseToJsonElement(File("../../contracts/fixtures/duration-cases.json").readText())
            .jsonObject
        val duration = field("game_ticks", "duration")
        for (case in fixture.getValue("core").jsonArray.map { it.jsonObject }) {
            val input = case.getValue("input").jsonPrimitive.content
            val ticks = case.getValue("ticks").jsonPrimitive.long
            assertEquals(JsonPrimitive(ticks), DraftFieldValueParser.parse(duration, input), input)
        }
        for (case in fixture.getValue("client_rejects").jsonArray.map { it.jsonObject }) {
            val input = case.getValue("input").jsonPrimitive.content
            assertFailsWith<IllegalArgumentException>(input) { DraftFieldValueParser.parse(duration, input) }
        }
    }

    private fun field(
        valueKind: String,
        control: String,
        options: List<ChoiceOption> = emptyList(),
    ): FormField = FormField(
        id = "field",
        label = "Field",
        control = control,
        valueKind = valueKind,
        options = options,
        optionSource = if (control == "multi_choice") "approved_values" else null,
        origins = listOf("paper", "fabric"),
    )
}
