package com.redstonesquid.minecraft.core.command

/** One completion offered for a `/squid` argument. */
public data class SquidSuggestion(
    /** What is inserted when the candidate is chosen. */
    public val value: String,
    /** Optional hover text; Brigadier renders it beside the value. */
    public val tooltip: String? = null,
)

/** Which argument is being completed. */
public enum class SuggestionSlot {
    /** `/squid submit <target>` — an active draft to continue. */
    SUBMIT_TARGET,

    /** `/squid set <field>` — a form field that may be answered. */
    SET_FIELD,

    /** `/squid set <field> <value>` — a value the chosen field accepts. */
    SET_VALUE,

    /** `/squid unset <field>` — a field the draft currently answers. */
    UNSET_FIELD,
}

/**
 * Produce completions for one `/squid` argument.
 *
 * Implementations must use cached state and return without blocking. Platforms may call
 * this on their game thread as the player types; fetching choices here would stall ticks.
 */
public fun interface SquidSuggestions<S> {
    public fun candidates(source: S, slot: SuggestionSlot, field: String?): List<SquidSuggestion>
}

/** Offer nothing, so a platform can register the tree before wiring suggestions. */
public fun <S> noSuggestions(): SquidSuggestions<S> = SquidSuggestions { _, _, _ -> emptyList() }
