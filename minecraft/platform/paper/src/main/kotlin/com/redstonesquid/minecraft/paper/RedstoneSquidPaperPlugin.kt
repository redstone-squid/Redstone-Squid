package com.redstonesquid.minecraft.paper

import com.mojang.brigadier.arguments.StringArgumentType
import com.mojang.brigadier.context.CommandContext
import com.redstonesquid.minecraft.core.auth.EphemeralMinecraftSecretStore
import com.redstonesquid.minecraft.core.auth.PaperInstallationKey
import com.redstonesquid.minecraft.core.command.CommandAccess
import com.redstonesquid.minecraft.core.command.CommandActions
import com.redstonesquid.minecraft.core.command.CommandAudience
import com.redstonesquid.minecraft.core.command.SquidCommandAction
import com.redstonesquid.minecraft.core.command.SquidCommandTree
import com.redstonesquid.minecraft.core.http.BoundedJdkHttpTransport
import com.redstonesquid.minecraft.core.submission.MinecraftSubmissionWorkflow
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import io.papermc.paper.command.brigadier.CommandSourceStack
import io.papermc.paper.plugin.lifecycle.event.types.LifecycleEvents
import java.util.concurrent.Executor
import org.bukkit.entity.Player
import org.bukkit.plugin.java.JavaPlugin

public class RedstoneSquidPaperPlugin : JavaPlugin() {
    private var workflow: MinecraftSubmissionWorkflow? = null

    override fun onEnable() {
        val initialized = try {
            createWorkflow()
        } catch (_: Exception) {
            logger.severe(
                "Redstone Squid is disabled: configure valid HTTPS API/approval endpoints, a Paper " +
                    "installation ID, and its environment-only secret.",
            )
            server.pluginManager.disablePlugin(this)
            return
        }
        workflow = initialized
        val actions = PaperCommandActions(initialized)
        lifecycleManager.registerEventHandler(LifecycleEvents.COMMANDS) { event ->
            event.registrar().register(
                SquidCommandTree.build(PaperCommandAccess, actions).build(),
                "Create and edit a synchronized Redstone Squid draft",
            )
        }
        logger.info("Redstone Squid enabled with ephemeral player grants; grants will be lost on restart.")
    }

    override fun onDisable() {
        workflow?.close()
        workflow = null
    }

    private fun createWorkflow(): MinecraftSubmissionWorkflow {
        val settings = PaperRuntimeSettings.load(System::getProperty, System::getenv)
        val installationKey = PaperInstallationKey("paper:environment")
        val store = EphemeralMinecraftSecretStore().also {
            it.saveInstallation(installationKey, settings.installationCredential())
        }
        return MinecraftSubmissionWorkflow(
            origin = MinecraftOrigin.PAPER,
            transport = BoundedJdkHttpTransport(settings.endpoints.apiBaseUri),
            secretStore = store,
            approvalFallbackUri = settings.endpoints.approvalUri,
            callbackExecutor = Executor { task -> server.scheduler.runTask(this, task) },
            paperInstallationKey = installationKey,
        )
    }
}

private object PaperCommandAccess : CommandAccess<CommandSourceStack> {
    override fun canRun(source: CommandSourceStack, action: SquidCommandAction): Boolean =
        source.sender.hasPermission(action.permission) &&
            (action.audience != CommandAudience.PLAYER || source.sender is Player)
}

private class PaperCommandActions(
    private val workflow: MinecraftSubmissionWorkflow,
) : CommandActions<CommandSourceStack> {
    override fun execute(action: SquidCommandAction, context: CommandContext<CommandSourceStack>): Int {
        if (action.audience == CommandAudience.SERVER_ADMIN) {
            context.source.sender.sendPlainMessage(
                "Paper installation credentials are managed outside the game in this release.",
            )
            return 1
        }
        val player = context.source.sender as? Player
        if (player == null) {
            context.source.sender.sendPlainMessage("This Redstone Squid command must be run by a player.")
            return 1
        }
        val notify: (String) -> Unit = player::sendPlainMessage
        when (action) {
            SquidCommandAction.LINK -> workflow.link(player.uniqueId, notify)
            SquidCommandAction.SUBMIT -> workflow.submit(
                player.uniqueId,
                player.locale().toLanguageTag(),
                optionalArgument(context, "category"),
                notify,
            )
            SquidCommandAction.STATUS -> workflow.status(player.uniqueId, notify)
            SquidCommandAction.CANCEL -> workflow.cancel(player.uniqueId, player.locale().toLanguageTag(), notify)
            SquidCommandAction.SET -> {
                val field = optionalArgument(context, "field")
                val value = optionalArgument(context, "value")
                if (field == null || value == null) {
                    notify("Usage: /squid set <field> <value>")
                } else {
                    workflow.setField(player.uniqueId, player.locale().toLanguageTag(), field, value, notify)
                }
            }
            SquidCommandAction.UNSET -> {
                val field = optionalArgument(context, "field")
                if (field == null) {
                    notify("Usage: /squid unset <field>")
                } else {
                    workflow.unsetField(player.uniqueId, player.locale().toLanguageTag(), field, notify)
                }
            }
            SquidCommandAction.SELECT,
            SquidCommandAction.POSITION_ONE,
            SquidCommandAction.POSITION_TWO,
            SquidCommandAction.SELECTION,
            -> notify(
                "World capture and schematic upload are not available yet; no unsanitized schematic was created.",
            )
            SquidCommandAction.SERVER_CLAIM,
            SquidCommandAction.SERVER_STATUS,
            SquidCommandAction.SERVER_ROTATE,
            SquidCommandAction.SERVER_REVOKE,
            -> error("server admin action was handled before resolving a player")
        }
        return 1
    }

    private fun optionalArgument(context: CommandContext<CommandSourceStack>, name: String): String? =
        runCatching { StringArgumentType.getString(context, name) }.getOrNull()
}
