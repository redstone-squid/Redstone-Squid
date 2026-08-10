package com.redstonesquid.minecraft.core.command

import com.mojang.brigadier.Command
import com.mojang.brigadier.builder.LiteralArgumentBuilder
import com.mojang.brigadier.context.CommandContext

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
    SELECT("select", "redstonesquid.submit", CommandAudience.PLAYER),
    POSITION_ONE("pos1", "redstonesquid.submit", CommandAudience.PLAYER),
    POSITION_TWO("pos2", "redstonesquid.submit", CommandAudience.PLAYER),
    SELECTION("selection", "redstonesquid.submit", CommandAudience.PLAYER),
    CANCEL("cancel", "redstonesquid.submit", CommandAudience.PLAYER),
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
    public fun <S> build(access: CommandAccess<S>, actions: CommandActions<S>): LiteralArgumentBuilder<S> {
        val root = LiteralArgumentBuilder.literal<S>("squid")
        SquidCommandAction.entries
            .filter { it.audience == CommandAudience.PLAYER }
            .forEach { root.then(actionNode(it, access, actions)) }

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
}
