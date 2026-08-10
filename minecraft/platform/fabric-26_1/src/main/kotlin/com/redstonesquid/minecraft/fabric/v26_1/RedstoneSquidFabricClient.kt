package com.redstonesquid.minecraft.fabric.v26_1

import com.mojang.brigadier.arguments.StringArgumentType
import com.mojang.brigadier.context.CommandContext
import com.redstonesquid.minecraft.core.auth.EphemeralMinecraftSecretStore
import com.redstonesquid.minecraft.core.command.CommandAccess
import com.redstonesquid.minecraft.core.command.CommandActions
import com.redstonesquid.minecraft.core.command.CommandAudience
import com.redstonesquid.minecraft.core.command.SquidCommandAction
import com.redstonesquid.minecraft.core.command.SquidCommandTree
import com.redstonesquid.minecraft.core.http.BoundedJdkHttpTransport
import com.redstonesquid.minecraft.core.http.MinecraftClientEndpoints
import com.redstonesquid.minecraft.core.submission.MinecraftSubmissionWorkflow
import com.redstonesquid.minecraft.protocol.MinecraftOrigin
import java.util.Locale
import java.util.concurrent.Executor
import net.fabricmc.api.ClientModInitializer
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents
import net.minecraft.client.Minecraft
import net.minecraft.network.chat.Component

public class RedstoneSquidFabricClient : ClientModInitializer {
    private var workflow: MinecraftSubmissionWorkflow? = null

    override fun onInitializeClient() {
        val initialized = try {
            createWorkflow()
        } catch (_: Exception) {
            null
        }
        workflow = initialized
        if (initialized == null) {
            System.err.println(
                "Redstone Squid is inactive: configure valid HTTPS API and approval endpoints through " +
                    "environment variables or JVM system properties.",
            )
        }
        ClientCommandRegistrationCallback.EVENT.register { dispatcher, _ ->
            val actions: CommandActions<FabricClientCommandSource> = initialized?.let(::FabricCommandActions)
                ?: UnavailableFabricCommandActions
            dispatcher.register(SquidCommandTree.build(FabricCommandAccess, actions))
        }
        ClientLifecycleEvents.CLIENT_STOPPING.register { workflow?.close() }
    }

    private fun createWorkflow(): MinecraftSubmissionWorkflow {
        val endpoints = MinecraftClientEndpoints.parse(
            setting(API_BASE_PROPERTY, API_BASE_ENV),
            setting(APPROVAL_URI_PROPERTY, APPROVAL_URI_ENV),
        )
        return MinecraftSubmissionWorkflow(
            origin = MinecraftOrigin.FABRIC,
            transport = BoundedJdkHttpTransport(endpoints.apiBaseUri),
            secretStore = EphemeralMinecraftSecretStore(),
            approvalFallbackUri = endpoints.approvalUri,
            callbackExecutor = Executor { task -> Minecraft.getInstance().execute(task) },
        )
    }

    private fun setting(property: String, environment: String): String? =
        System.getProperty(property)?.trim()?.takeIf(String::isNotEmpty)
            ?: System.getenv(environment)?.trim()?.takeIf(String::isNotEmpty)

    private companion object {
        const val API_BASE_PROPERTY: String = "redstonesquid.apiBaseUri"
        const val API_BASE_ENV: String = "SQUID_MINECRAFT_API_BASE_URI"
        const val APPROVAL_URI_PROPERTY: String = "redstonesquid.approvalUri"
        const val APPROVAL_URI_ENV: String = "SQUID_MINECRAFT_APPROVAL_URI"
    }
}

private object FabricCommandAccess : CommandAccess<FabricClientCommandSource> {
    override fun canRun(source: FabricClientCommandSource, action: SquidCommandAction): Boolean =
        action.audience == CommandAudience.PLAYER
}

private class FabricCommandActions(
    private val workflow: MinecraftSubmissionWorkflow,
) : CommandActions<FabricClientCommandSource> {
    override fun execute(action: SquidCommandAction, context: CommandContext<FabricClientCommandSource>): Int {
        val source = context.source
        val playerId = source.player.uuid
        val locale = Locale.getDefault().toLanguageTag()
        val notify: (String) -> Unit = { source.sendFeedback(Component.literal(it)) }
        when (action) {
            SquidCommandAction.LINK -> workflow.link(playerId, notify)
            SquidCommandAction.SUBMIT -> workflow.submit(
                playerId,
                locale,
                optionalArgument(context, "category"),
                notify,
            )
            SquidCommandAction.STATUS -> workflow.status(playerId, notify)
            SquidCommandAction.CANCEL -> workflow.cancel(playerId, locale, notify)
            SquidCommandAction.SET -> {
                val field = optionalArgument(context, "field")
                val value = optionalArgument(context, "value")
                if (field == null || value == null) {
                    notify("Usage: /squid set <field> <value>")
                } else {
                    workflow.setField(playerId, locale, field, value, notify)
                }
            }
            SquidCommandAction.UNSET -> {
                val field = optionalArgument(context, "field")
                if (field == null) {
                    notify("Usage: /squid unset <field>")
                } else {
                    workflow.unsetField(playerId, locale, field, notify)
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
            -> notify("Server installation commands are unavailable in the Fabric client.")
        }
        return 1
    }

    private fun optionalArgument(context: CommandContext<FabricClientCommandSource>, name: String): String? =
        runCatching { StringArgumentType.getString(context, name) }.getOrNull()
}

private object UnavailableFabricCommandActions : CommandActions<FabricClientCommandSource> {
    override fun execute(action: SquidCommandAction, context: CommandContext<FabricClientCommandSource>): Int {
        context.source.sendFeedback(
            Component.literal("Redstone Squid is inactive because its HTTPS API/approval endpoints are not configured."),
        )
        return 1
    }
}
