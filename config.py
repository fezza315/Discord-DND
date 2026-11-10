"""Fill these two in, then run:  python main.py

You can also leave them blank and set the environment variables
DISCORD_TOKEN and DISCORD_GUILD_ID instead.
"""
import os

BOT_TOKEN = ""   # <- your bot's token  (Discord developer portal -> Bot -> Reset Token)
GUILD_ID = 0     # <- your server's ID  (Discord: Settings -> Advanced -> Developer Mode, right-click server -> Copy Server ID)

BOT_TOKEN = BOT_TOKEN or os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = GUILD_ID or int(os.environ.get("DISCORD_GUILD_ID", "0") or 0)
