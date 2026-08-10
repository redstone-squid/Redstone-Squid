package com.redstonesquid.minecraft.core.auth

/** Test-only adapter; production platforms must use an audited OS credential vault. */
class InMemoryMinecraftSecretStore : MinecraftSecretStore {
    private val installations = mutableMapOf<PaperInstallationKey, PaperInstallationCredential>()
    private val grants = mutableMapOf<PlayerGrantKey, PlayerGrantCredential>()

    override fun loadInstallation(key: PaperInstallationKey): PaperInstallationCredential? = installations[key]

    override fun saveInstallation(key: PaperInstallationKey, credential: PaperInstallationCredential) {
        installations[key] = credential
    }

    override fun removeInstallation(key: PaperInstallationKey) {
        installations.remove(key)
    }

    override fun loadPlayerGrant(key: PlayerGrantKey): PlayerGrantCredential? = grants[key]

    override fun savePlayerGrant(credential: PlayerGrantCredential) {
        grants[credential.key] = credential
    }

    override fun removePlayerGrant(key: PlayerGrantKey) {
        grants.remove(key)
    }
}
