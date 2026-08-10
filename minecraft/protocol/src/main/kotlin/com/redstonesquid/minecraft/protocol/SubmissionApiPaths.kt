package com.redstonesquid.minecraft.protocol

/** Canonical paths relative to the backend's `/v1` prefix. */
public object SubmissionApiPaths {
    public const val CURRENT_FORM: String = "/submissions/form/current"
    public const val FORM_OPTIONS_TEMPLATE: String = "/submissions/form/options/{source}"
    public const val DRAFTS: String = "/submissions/drafts"
    public const val DRAFT_TEMPLATE: String = "/submissions/drafts/{id}"
    public const val DRAFT_CHANGES_TEMPLATE: String = "/submissions/drafts/{id}/changes"
}

/** Canonical Minecraft authorization paths relative to the backend's `/v1` prefix. */
public object MinecraftAuthApiPaths {
    public const val PAPER_CHALLENGES: String = "/minecraft/auth/paper/challenges"
    public const val PAPER_EXCHANGE: String = "/minecraft/auth/paper/challenges/exchange"
    public const val FABRIC_CHALLENGES: String = "/minecraft/auth/fabric/challenges"
    public const val FABRIC_EXCHANGE: String = "/minecraft/auth/fabric/challenges/exchange"
}
