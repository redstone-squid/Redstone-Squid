from time import gmtime, strftime
import discord

def represents_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False

def represents_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def represents_user(s):
    if (s[:2] != '<@' and s[:3] != '<@!') or s[-1] != '>':
        return False
    chars = '!<@>'
    for c in chars:
        s = s.replace(c, '')
    if not represents_int(s):
        return False
    return True

def get_time():
    raw_time = strftime("%Y/%m/%d %H:%M:%S", gmtime())
    return '[' + raw_time + '] '

def error_embed(title, description):
    return discord.Embed(title = title, colour = 0xF04747, description = ':x: ' + description)

def warning_embed(title, description):
    return discord.Embed(title = ':warning: ' + title, colour = 0xFAA61A, description = description)