package com.redstonesquid.minecraft.core.submission

import com.redstonesquid.minecraft.protocol.ChoiceOption
import com.redstonesquid.minecraft.protocol.FormField
import java.math.BigDecimal
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull

/**
 * Parses `/squid set` values and checks the field's constraints and approved choices.
 *
 * Lists use commas with trimmed, nonempty, unique entries. Durations require a suffix:
 * `gt`/`t` are game ticks, `rt` is two game ticks, and `s` is twenty. Fractional results
 * are rejected. Supplied dynamic options replace inline options; invalid input throws
 * [IllegalArgumentException]. The backend still validates the resulting answer.
 */
public object DraftFieldValueParser {
    public fun parse(field: FormField, rawValue: String, dynamicOptions: List<ChoiceOption>? = null): JsonElement {
        require(rawValue.length <= 4_000) { "value is longer than the in-game editor supports" }
        val value = when (field.valueKind) {
            "string" -> JsonPrimitive(rawValue)
            "integer" -> JsonPrimitive(rawValue.toLongOrNull() ?: invalid(field, "expected a whole number"))
            "number" -> {
                val number = rawValue.toDoubleOrNull()?.takeIf(Double::isFinite)
                    ?: invalid(field, "expected a finite number")
                JsonPrimitive(number)
            }
            "boolean" -> JsonPrimitive(parseBoolean(field, rawValue))
            "credit_list" -> JsonArray(parseList(field, rawValue).map { entry ->
                val parts = entry.split(" | ", limit = 2)
                val reference = parts[0]
                val prefix = reference.substringBefore(':', "")
                val kind = when (prefix) {
                    "discord" -> "discord"
                    "java" -> "minecraft_java"
                    "bedrock" -> "minecraft_bedrock"
                    else -> "name"
                }
                val name = if (kind == "name") reference else reference.substringAfter(':').removePrefix("@")
                buildJsonObject {
                    put("name", name)
                    put("kind", kind)
                    put("role", parts.getOrElse(1) { "creator" })
                    if (kind != "name") put("username", name)
                }
            })
            "string_list" -> JsonArray(parseList(field, rawValue).map(::JsonPrimitive))
            "game_ticks" -> JsonPrimitive(parseGameTicks(field, rawValue))
            else -> invalid(field, "value kind '${field.valueKind}' is not supported in-game")
        }
        validateConstraints(field, value)
        validateOptions(field, value, dynamicOptions)
        return value
    }

    private fun parseBoolean(field: FormField, rawValue: String): Boolean = when (rawValue.lowercase()) {
        "true", "yes", "on" -> true
        "false", "no", "off" -> false
        else -> invalid(field, "expected true or false")
    }

    private fun parseList(field: FormField, rawValue: String): List<String> {
        val values = rawValue.split(',').map(String::trim)
        require(values.isNotEmpty() && values.none(String::isEmpty)) {
            "${field.id}: expected one or more comma-separated values"
        }
        require(values.distinct().size == values.size) { "${field.id}: duplicate list values are not allowed" }
        return values
    }

    private fun parseGameTicks(field: FormField, rawValue: String): Long {
        val match = DURATION.matchEntire(rawValue.trim().lowercase())
            ?: invalid(field, "expected game ticks (10gt), redstone ticks (5rt), or seconds (1.5s)")
        val amount = match.groupValues[1].toBigDecimal()
        val multiplier = when (match.groupValues[2]) {
            "gt", "t" -> BigDecimal.ONE
            "rt" -> BigDecimal(2)
            "s" -> BigDecimal(20)
            else -> error("duration suffix regex and parser diverged")
        }
        return runCatching { amount.multiply(multiplier).longValueExact() }
            .getOrElse { invalid(field, "duration must equal a whole number of game ticks") }
    }

    private fun validateConstraints(field: FormField, value: JsonElement) {
        val constraints = field.constraints
        if (constraints.mustEqual != null) {
            require(value == constraints.mustEqual) { "${field.id}: value must equal ${constraints.mustEqual}" }
        }
        val primitive = value as? JsonPrimitive
        val string = primitive?.takeIf { it.isString }?.content
        val minLength = constraints.minLength
        val maxLength = constraints.maxLength
        string?.let {
            require(minLength == null || it.length >= minLength) {
                "${field.id}: value is shorter than $minLength characters"
            }
            require(maxLength == null || it.length <= maxLength) {
                "${field.id}: value is longer than $maxLength characters"
            }
        }
        val list = value as? JsonArray
        val minItems = constraints.minItems
        val maxItems = constraints.maxItems
        list?.let {
            require(minItems == null || it.size >= minItems) {
                "${field.id}: fewer than $minItems values were provided"
            }
            require(maxItems == null || it.size <= maxItems) {
                "${field.id}: more than $maxItems values were provided"
            }
        }
        val number = primitive?.longOrNull?.toDouble() ?: primitive?.doubleOrNull
        val minimum = constraints.minimum
        val maximum = constraints.maximum
        number?.let {
            require(minimum == null || it >= minimum) {
                "${field.id}: value must be at least $minimum"
            }
            require(maximum == null || it <= maximum) {
                "${field.id}: value must be at most $maximum"
            }
        }
    }

    private fun validateOptions(field: FormField, value: JsonElement, dynamicOptions: List<ChoiceOption>?) {
        if (field.control != "choice" && field.control != "multi_choice") {
            return
        }
        val options = dynamicOptions ?: field.options
        require(options.isNotEmpty()) { "${field.id}: approved options could not be loaded" }
        val allowed = options.map(ChoiceOption::value).toSet()
        val values = when (value) {
            is JsonArray -> value.map { (it as JsonPrimitive).content }
            is JsonPrimitive -> listOf(value.content)
            else -> emptyList()
        }
        require(values.all(allowed::contains)) {
            "${field.id}: choose from ${options.joinToString { it.value }}"
        }
    }

    private fun invalid(field: FormField, detail: String): Nothing = throw IllegalArgumentException(
        "${field.id}: $detail",
    )

    // Unit vocabulary shared with the web catalogue and the backend's free-text parser;
    // contracts/fixtures/duration-cases.json is the authority and the parser test asserts it.
    private val DURATION = Regex("([0-9]+(?:\\.[0-9]+)?|\\.[0-9]+)\\s*(gt|rt|t|s)")
}
