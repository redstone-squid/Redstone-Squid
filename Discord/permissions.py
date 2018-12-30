CREATE_INSTANT_INVITE =     0x00000001
KICK_MEMBERS =              0x00000002
BAN_MEMBERS =               0x00000004
ADMINISTRATOR =             0x00000008
MANAGE_CHANNELS =           0x00000010
ADD_REACTIONS =             0x00000040
VIEW_AUDIT_LOG =            0x00000080
SEND_MESSAGES =             0x00000800
SEND_TTS_MESSAGES =         0x00001000
MANAGE_MESSAGES =           0x00002000
EMBED_LINKS =               0x00004000
ATTACH_FILES =              0x00008000
READ_MESSAGE_HISTORY =      0x00010000
MENTION_EVERYONE =          0x00020000
USE_EXTERNAL_EMOJIS =       0x00040000
CONNECT =                   0x00100000
SPEAK =                     0x00200000
MUTE_MEMBERS =              0x00400000
DEAFEN_MEMBERS =            0x00800000
MOVE_MEMBERS =              0x01000000
USE_VAD =                   0x02000000
CHANGE_NICKNAME =           0x04000000
MANAGE_NICKNAMES =          0x08000000
MANAGE_ROLES =              0x10000000
MANAGE_WEBHOOKS =           0x20000000
MANAGE_EMOJIS =             0x40000000

def validate_permissions(user_perms, cmd_perms):
    if not cmd_perms:
        return True

    for perm in cmd_perms:
            if perm == CREATE_INSTANT_INVITE and not user_perms.create_instant_invite:
                return False
            if perm == KICK_MEMBERS and not user_perms.kick_members:
                return False
            if perm == BAN_MEMBERS and not user_perms.ban_members:
                return False
            if perm == ADMINISTRATOR and not user_perms.administrator:
                return False
            if perm == MANAGE_CHANNELS and not user_perms.manage_channels:
                return False
            if perm == ADD_REACTIONS and not user_perms.add_reactions:
                return False
            if perm == VIEW_AUDIT_LOG and not user_perms.view_audit_logs:
                return False
            if perm == SEND_MESSAGES and not user_perms.send_messages:
                return False
            if perm == SEND_TTS_MESSAGES and not user_perms.send_tts_messages:
                return False
            if perm == MANAGE_MESSAGES and not user_perms.manage_messages:
                return False
            if perm == EMBED_LINKS and not user_perms.embed_links:
                return False
            if perm == ATTACH_FILES and not user_perms.attach_files:
                return False
            if perm == READ_MESSAGE_HISTORY and not user_perms.read_message_history:
                return False
            if perm == MENTION_EVERYONE and not user_perms.mention_everyone:
                return False
            if perm == USE_EXTERNAL_EMOJIS and not user_perms.external_emojis:
                return False
            if perm == CONNECT and not user_perms.connect:
                return False
            if perm == SPEAK and not user_perms.speak:
                return False
            if perm == MUTE_MEMBERS and not user_perms.mute_members:
                return False
            if perm == DEAFEN_MEMBERS and not user_perms.deafen_members:
                return False
            if perm == MOVE_MEMBERS and not user_perms.move_members:
                return False
            if perm == USE_VAD and not user_perms.use_voice_activation:
                return False
            if perm == CHANGE_NICKNAME and not user_perms.change_nickname:
                return False
            if perm == MANAGE_NICKNAMES and not user_perms.manage_nicknames:
                return False
            if perm == MANAGE_ROLES and not user_perms.manage_roles:
                return False
            if perm == MANAGE_WEBHOOKS and not user_perms.manage_webhooks:
                return False
            if perm == MANAGE_EMOJIS and not user_perms.manage_emojis:
                return False
    return True

        