package com.redstonesquid.minecraft.fabric.v26_1

import com.mojang.brigadier.context.CommandContext
import com.redstonesquid.minecraft.core.command.CommandAccess
import com.redstonesquid.minecraft.core.command.CommandActions
import com.redstonesquid.minecraft.core.command.CommandAudience
import com.redstonesquid.minecraft.core.command.SquidCommandAction
import com.redstonesquid.minecraft.core.command.SquidCommandTree
import net.fabricmc.api.ClientModInitializer
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource
import net.minecraft.network.chat.Component

public class RedstoneSquidFabricClient : ClientModInitializer {
    override fun onInitializeClient() {
        ClientCommandRegistrationCallback.EVENT.register { dispatcher, _ ->
            dispatcher.register(SquidCommandTree.build(FabricCommandAccess, FabricCommandActions))
        }
    }
}

private object FabricCommandAccess : CommandAccess<FabricClientCommandSource> {
    override fun canRun(source: FabricClientCommandSource, action: SquidCommandAction): Boolean =
        action.audience == CommandAudience.PLAYER
}

private object FabricCommandActions : CommandActions<FabricClientCommandSource> {
    override fun execute(
        action: SquidCommandAction,
        context: CommandContext<FabricClientCommandSource>,
    ): Int {
        val message = "Redstone Squid '${action.literal}' is registered, " +
            "but the draft service is not connected in this milestone."
        context.source.sendFeedback(Component.literal(message))
        return 1
    }
}
