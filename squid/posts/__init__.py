"""Bot-owned Discord post bounded context.

A post is a Discord message the bot sent and keeps rendered: a build card, a vote
card, a starboard entry. Distinct from `squid.messages`, which records messages as
facts regardless of who sent them or why.
"""
