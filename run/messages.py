from .glob_variables import BotState
from .buttons import Buttons
from utils import db, TweetCapture
from telethon.errors.rpcerrorlist import MessageNotModifiedError


class BotMessageHandler:
    start_message = """
🎧 **Welcome to Apex Music Downloader!**  

Send me the name of a song, artist, or paste a link —  
and I’ll fetch the best-quality version for you. ✨  

Use /help or tap the **Instructions** button below to explore more. ⬇️
"""

    instruction_message = """
🎶 **Apex Music Downloader – Quick Guide**

**Spotify / YouTube Music / Shazam**  
1. Send any song or playlist link 🔗  
2. Wait for confirmation ⏳  
3. Receive your audio instantly 💾  
4. You can also send a short voice sample 🎤 for best match  
5. Ask for lyrics, artist info, or related songs 📜  

---

🎥 **YouTube Video Downloader**  
1. Paste a YouTube video link 🔗  
2. Choose preferred quality (if asked)  
3. Receive your video file 📦  

---

📸 **Instagram Downloader**  
1. Share any post, Reel, or IGTV link 🔗  
2. Wait for download ⏳  
3. Get your media delivered 💬  

---

🐦 **Tweet Capture**  
1. Paste a tweet link 🔗  
2. Receive a clean screenshot 🖼️  
3. Use “Download Media” for attached content 📥  

💡 *Tip: Search songs by title, lyrics, or even mood!*  

For queries, contact **@apexservers**
"""

    search_result_message = """🎵 **Top matches for your search:**"""

    core_selection_message = """⚙️ **Select Your Preferred Download Engine**"""

    JOIN_CHANNEL_MESSAGE = """
🚪 You need to join our official channel before using the bot.  
Please join and try again.
"""

    search_playlist_message = """🎧 **This playlist includes:**"""

    @staticmethod
    async def send_message(event, text, buttons=None):
        chat_id = event.chat_id
        user_id = event.sender_id
        await BotState.initialize_user_state(user_id)
        await BotState.BOT_CLIENT.send_message(chat_id, text, buttons=buttons)

    @staticmethod
    async def edit_message(event, message_text, buttons=None):
        user_id = event.sender_id
        await BotState.initialize_user_state(user_id)
        try:
            await event.edit(message_text, buttons=buttons)
        except MessageNotModifiedError:
            pass

    @staticmethod
    async def edit_quality_setting_message(e):
        music_quality = await db.get_user_music_quality(e.sender_id)
        if music_quality:
            message = (
                f"🎚️ **Your Quality Setting**\n"
                f"Format: {music_quality['format']}\n"
                f"Quality: {music_quality['quality']}\n\n"
                f"Available Options ↓"
            )
        else:
            message = "⚙️ No quality settings found."
        await BotMessageHandler.edit_message(e, message, buttons=Buttons.get_quality_setting_buttons(music_quality))

    @staticmethod
    async def edit_core_setting_message(e):
        downloading_core = await db.get_user_downloading_core(e.sender_id)
        if downloading_core:
            message = f"{BotMessageHandler.core_selection_message}\n\nCurrent Core: `{downloading_core}`"
        else:
            message = f"{BotMessageHandler.core_selection_message}\n\nNo core selected yet."
        await BotMessageHandler.edit_message(e, message, buttons=Buttons.get_core_setting_buttons(downloading_core))

    @staticmethod
    async def edit_subscription_status_message(e):
        is_subscribed = await db.is_user_subscribed(e.sender_id)
        message = f"💎 **Subscription Settings**\n\nCurrent Status: `{is_subscribed}`"
        await BotMessageHandler.edit_message(e, message, buttons=Buttons.get_subscription_setting_buttons(is_subscribed))

    @staticmethod
    async def edit_tweet_capture_setting_message(e):
        night_mode = await TweetCapture.get_settings(e.sender_id)
        mode = night_mode['night_mode']
        mode_to_show = "Light"
        match mode:
            case "1":
                mode_to_show = "Dark"
            case "2":
                mode_to_show = "Black"
        message = f"🐦 **Tweet Capture Settings**\n\nCurrent Mode: `{mode_to_show}`"
        await BotMessageHandler.edit_message(e, message, buttons=Buttons.get_tweet_capture_setting_buttons(mode))
