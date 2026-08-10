package com.redstonesquid.minecraft.paper

import com.redstonesquid.minecraft.core.command.CommandAccess
import com.redstonesquid.minecraft.core.command.CommandActions
import com.redstonesquid.minecraft.core.command.SquidCommandAction
import com.redstonesquid.minecraft.core.command.SquidCommandTree
import io.papermc.paper.command.brigadier.CommandSourceStack
import io.papermc.paper.plugin.lifecycle.event.types.LifecycleEvents
import org.bukkit.plugin.java.JavaPlugin

public class RedstoneSquidPaperPlugin : JavaPlugin() {
    override fun onEnable() {
        lifecycleManager.registerEventHandler(LifecycleEvents.COMMANDS) { event ->
            event.registrar().register(
                SquidCommandTree.build(PaperCommandAccess, PaperCommandActions).build(),
                "Capture and submit a Redstone Squid build",
            )
        }
    }
}

private object PaperCommandAccess : CommandAccess<CommandSourceStack> {
    override fun canRun(source: CommandSourceStack, action: SquidCommandAction): Boolean =
        source.sender.hasPermission(action.permission)
}

private object PaperCommandActions : CommandActions<CommandSourceStack> {
    override fun execute(
        action: SquidCommandAction,
        context: com.mojang.brigadier.context.CommandContext<CommandSourceStack>,
    ): Int {
        val message = "Redstone Squid '${action.literal}' is registered, " +
            "but the draft service is not connected in this milestone."
        context.source.sender.sendPlainMessage(message)
        return 1
    }
}
