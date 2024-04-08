import discord
from time import gmtime, strftime

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def get_time():
    raw_time = strftime("%Y/%m/%d %H:%M:%S", gmtime())
    return '[' + raw_time + '] '


def error_embed(title, description):
    return discord.Embed(title=title, colour=discord_red, description=':x: ' + description)


def warning_embed(title, description):
    return discord.Embed(title=':warning: ' + title, colour=discord_yellow, description=description)


def info_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)
