package com.redstonesquid.minecraft.core.command

import com.mojang.brigadier.Command
import com.mojang.brigadier.LiteralMessage
import com.mojang.brigadier.arguments.StringArgumentType
import com.mojang.brigadier.builder.LiteralArgumentBuilder
import com.mojang.brigadier.builder.RequiredArgumentBuilder
import com.mojang.brigadier.context.CommandContext
import com.mojang.brigadier.suggestion.SuggestionProvider

public enum class CommandAudience {
    PLAYER,
    SERVER_ADMIN,
}

public enum class SquidCommandAction(
    public val literal: String,
    public val permission: String,
    public val audience: CommandAudience,
) {
    SUBMIT("submit", "redstonesquid.submit", CommandAudience.PLAYER),
    SET("set", "redstonesquid.submit", CommandAudience.PLAYER),
    UNSET("unset", "redstonesquid.submit", CommandAudience.PLAYER),
    SELECT("select", "redstonesquid.submit", CommandAudience.PLAYER),
    POSITION_ONE("pos1", "redstonesquid.submit", CommandAudience.PLAYER),
    POSITION_TWO("pos2", "redstonesquid.submit", CommandAudience.PLAYER),
    SELECTION("selection", "redstonesquid.submit", CommandAudience.PLAYER),
    CANCEL("cancel", "redstonesquid.submit", CommandAudience.PLAYER),
    SETTINGS("settings", "redstonesquid.use", CommandAudience.PLAYER),
    STATUS("status", "redstonesquid.use", CommandAudience.PLAYER),
    LINK("link", "redstonesquid.use", CommandAudience.PLAYER),
    SERVER_CLAIM("claim", "redstonesquid.server.manage", CommandAudience.SERVER_ADMIN),
    SERVER_STATUS("status", "redstonesquid.server.manage", CommandAudience.SERVER_ADMIN),
    SERVER_ROTATE("rotate", "redstonesquid.server.manage", CommandAudience.SERVER_ADMIN),
    SERVER_REVOKE("revoke", "redstonesquid.server.manage", CommandAudience.SERVER_ADMIN),
}

public fun interface CommandAccess<S> {
    public fun canRun(source: S, action: SquidCommandAction): Boolean
}

public fun interface CommandActions<S> {
    public fun execute(action: SquidCommandAction, context: CommandContext<S>): Int
}

/** Builds one native Brigadier tree for Paper and Fabric to register themselves. */
public object SquidCommandTree {
    public fun <S> build(
        access: CommandAccess<S>,
        actions: CommandActions<S>,
        suggestions: SquidSuggestions<S> = noSuggestions(),
    ): LiteralArgumentBuilder<S> {
        val root = LiteralArgumentBuilder.literal<S>("squid")
        SquidCommandAction.entries
            .filter { it.audience == CommandAudience.PLAYER }
            .forEach { action -> root.then(playerActionNode(action, access, actions, suggestions)) }

        val server = LiteralArgumentBuilder.literal<S>("server")
            .requires { source ->
                SquidCommandAction.entries
                    .filter { it.audience == CommandAudience.SERVER_ADMIN }
                    .any { access.canRun(source, it) }
            }
        SquidCommandAction.entries
            .filter { it.audience == CommandAudience.SERVER_ADMIN }
            .forEach { server.then(actionNode(it, access, actions)) }
        root.then(server)
        return root
    }

    private fun <S> actionNode(
        action: SquidCommandAction,
        access: CommandAccess<S>,
        actions: CommandActions<S>,
    ): LiteralArgumentBuilder<S> = LiteralArgumentBuilder.literal<S>(action.literal)
        .requires { access.canRun(it, action) }
        .executes {
            val result = actions.execute(action, it)
            if (result == 0) Command.SINGLE_SUCCESS else result
        }

    private fun <S> playerActionNode(
        action: SquidCommandAction,
        access: CommandAccess<S>,
        actions: CommandActions<S>,
        suggestions: SquidSuggestions<S>,
    ): LiteralArgumentBuilder<S> {
        val node = actionNode(action, access, actions)
        when (action) {
            SquidCommandAction.SUBMIT -> node.then(
                RequiredArgumentBuilder.argument<S, String>(
                    "target",
                    StringArgumentType.word(),
                ).suggests(provider(suggestions, SuggestionSlot.SUBMIT_TARGET))
                    .executes { actions.execute(action, it) },
            )
            SquidCommandAction.SETTINGS -> node.then(
                RequiredArgumentBuilder.argument<S, String>("name", StringArgumentType.word())
                    .executes { actions.execute(action, it) }
                    .then(
                        RequiredArgumentBuilder.argument<S, String>("value", StringArgumentType.greedyString())
                            .executes { actions.execute(action, it) },
                    ),
            )
            SquidCommandAction.SET -> node.then(
                RequiredArgumentBuilder.argument<S, String>(
                    "field",
                    StringArgumentType.word(),
                ).suggests(provider(suggestions, SuggestionSlot.SET_FIELD)).then(
                    RequiredArgumentBuilder.argument<S, String>(
                        "value",
                        StringArgumentType.greedyString(),
                    ).suggests(provider(suggestions, SuggestionSlot.SET_VALUE))
                        .executes { actions.execute(action, it) },
                ),
            )
            SquidCommandAction.UNSET -> node.then(
                RequiredArgumentBuilder.argument<S, String>(
                    "field",
                    StringArgumentType.word(),
                ).suggests(provider(suggestions, SuggestionSlot.UNSET_FIELD))
                    .executes { actions.execute(action, it) },
            )
            else -> Unit
        }
        return node
    }

    /**
     * Adapts cached candidates to Brigadier with case-insensitive prefix matching.
     * Candidate lookup failures produce an empty dropdown so typing can continue.
     */
    private fun <S> provider(
        suggestions: SquidSuggestions<S>,
        slot: SuggestionSlot,
    ): SuggestionProvider<S> = SuggestionProvider { context, builder ->
        val typed = builder.remainingLowerCase
        val candidates = runCatching {
            suggestions.candidates(context.source, slot, fieldArgument(context))
        }.getOrDefault(emptyList())
        candidates
            .filter { it.value.lowercase().startsWith(typed) }
            .forEach { candidate ->
                val tooltip = candidate.tooltip
                if (tooltip == null) {
                    builder.suggest(candidate.value)
                } else {
                    builder.suggest(candidate.value, LiteralMessage(tooltip))
                }
            }
        builder.buildFuture()
    }

    /**
     * Returns the preceding field argument for value completion.
     * Brigadier throws for slots without a field argument; those use null instead.
     */
    private fun <S> fieldArgument(context: CommandContext<S>): String? =
        runCatching { StringArgumentType.getString(context, "field") }.getOrNull()
}
