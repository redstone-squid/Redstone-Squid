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
 * Implementations must return immediately from whatever they already hold in memory. Brigadier
 * asks for suggestions on the server thread as the player types, so a blocking call here would
 * stall the tick loop — which is why nothing in the default implementation touches the network.
 */
public fun interface SquidSuggestions<S> {
    public fun candidates(source: S, slot: SuggestionSlot, field: String?): List<SquidSuggestion>
}

/** Offer nothing, so a platform can register the tree before wiring suggestions. */
public fun <S> noSuggestions(): SquidSuggestions<S> = SquidSuggestions { _, _, _ -> emptyList() }
