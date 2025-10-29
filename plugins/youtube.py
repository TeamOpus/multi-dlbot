import aiohttp
import asyncio
from utils import YoutubeDL, re, lru_cache, hashlib, InputMediaPhotoExternal, db
from utils import os, InputMediaUploadedDocument, DocumentAttributeVideo, fast_upload
from utils import DocumentAttributeAudio, DownloadError, WebpageMediaEmptyError
from run import Button, Buttons


class YoutubeDownloader:

    @classmethod
    def initialize(cls):
        cls.MAXIMUM_DOWNLOAD_SIZE_MB = 100
        cls.DOWNLOAD_DIR = 'repository/Youtube'

        if not os.path.isdir(cls.DOWNLOAD_DIR):
            os.mkdir(cls.DOWNLOAD_DIR)

    @lru_cache(maxsize=128)
    def get_file_path(url, format_id, extension):
        url = url + format_id + extension
        url_hash = hashlib.blake2b(url.encode()).hexdigest()
        filename = f"{url_hash}.{extension}"
        return os.path.join(YoutubeDownloader.DOWNLOAD_DIR, filename)

    @staticmethod
    def is_youtube_link(url):
        youtube_patterns = [
            r'(https?\:\/\/)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11}).*',
            r'(https?\:\/\/)?www\.youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?youtu\.be\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/embed\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/v\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/[^\/]+\?v=([a-zA-Z0-9_-]{11})(?!.*list=)',
        ]
        for pattern in youtube_patterns:
            match = re.match(pattern, url)
            if match:
                return True
        return False

    @staticmethod
    def extract_youtube_url(text):
        youtube_patterns = [
            r'(https?\:\/\/)?youtube\.com\/shorts\/([a-zA-Z0-9_-]{11}).*',
            r'(https?\:\/\/)?www\.youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?youtu\.be\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/embed\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/v\/([a-zA-Z0-9_-]{11})(?!.*list=)',
            r'(https?\:\/\/)?www\.youtube\.com\/[^\/]+\?v=([a-zA-Z0-9_-]{11})(?!.*list=)',
        ]

        for pattern in youtube_patterns:
            match = re.search(pattern, text)
            if match:
                video_id = match.group(2)
                if 'youtube.com/shorts/' in match.group(0):
                    return f'https://www.youtube.com/shorts/{video_id}'
                else:
                    return f'https://www.youtube.com/watch?v={video_id}'
        return None

    @staticmethod
    def _get_formats(url):
        ydl_opts = {
            'listformats': True,
            'no_warnings': True,
            'quiet': True,
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = info['formats']
        return formats

    @staticmethod
    async def send_youtube_info(client, event, youtube_link):
        url = youtube_link
        video_id = (youtube_link.split("?si=")[0]
                    .replace("https://www.youtube.com/watch?v=", "")
                    .replace("https://www.youtube.com/shorts/", ""))

        with YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            thumbnail_url = info['thumbnail']
            title = info.get('title', 'Unknown Title')

        # API-based formats
        video_buttons = [
            [Button.inline("🎬 MP4 (Video)", data=f"ytapi/{video_id}/mp4")]
        ]
        audio_buttons = [
            [Button.inline("🎧 MP3 (Audio)", data=f"ytapi/{video_id}/mp3")]
        ]

        buttons = video_buttons + audio_buttons
        buttons.append(Buttons.cancel_button)

        thumbnail = InputMediaPhotoExternal(thumbnail_url)
        thumbnail.ttl_seconds = 0

        try:
            await client.send_file(
                event.chat_id,
                file=thumbnail,
                caption=f"🎵 **{title}**\nSelect a format to download:",
                buttons=buttons
            )
        except WebpageMediaEmptyError:
            await event.respond(
                f"🎵 **{title}**\nSelect a format to download:",
                buttons=buttons
            )

    @staticmethod
    async def download_and_send_yt_file(client, event):
        user_id = event.sender_id

        if await db.get_file_processing_flag(user_id):
            return await event.respond("⚙️ Please wait — another file is being processed for you.")

        data = event.data.decode('utf-8')
        parts = data.split('/')
        if len(parts) == 3 and parts[0] == 'ytapi':
            video_id = parts[1]
            format_type = parts[2]  # mp3 or mp4

            await db.set_file_processing_flag(user_id, is_processing=True)
            waiting_msg = await event.respond(f"🎧 Fetching {format_type.upper()} link, please wait up to 90s...")

            api_url = f"https://apex.srvopus.workers.dev/arytmp?direct&id={video_id}&format={format_type}"

            # Fetch API response (wait up to 90 seconds)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, timeout=90) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                        else:
                            raise Exception(f"API returned {resp.status}")
            except asyncio.TimeoutError:
                await db.set_file_processing_flag(user_id, is_processing=False)
                return await waiting_msg.edit("⏳ API took too long to respond (timeout 90s). Try again.")
            except Exception as e:
                await db.set_file_processing_flag(user_id, is_processing=False)
                return await waiting_msg.edit(f"❌ Failed to fetch download link.\nReason: {str(e)}")

            # Validate API result
            if not result.get("status") == "success" or not result.get("download_url"):
                await db.set_file_processing_flag(user_id, is_processing=False)
                return await waiting_msg.edit("⚠️ API did not return a valid download URL.")

            download_url = result["download_url"]
            title = result.get("title", "Downloaded File")

            path = os.path.join(YoutubeDownloader.DOWNLOAD_DIR, f"{video_id}.{format_type}")

            # Download file from API
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(download_url, timeout=90) as r:
                        if r.status != 200:
                            raise Exception(f"Download failed with HTTP {r.status}")
                        with open(path, 'wb') as f:
                            while True:
                                chunk = await r.content.read(1024 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
            except Exception as e:
                await db.set_file_processing_flag(user_id, is_processing=False)
                return await waiting_msg.edit(f"⚠️ Could not download file.\nReason: {str(e)}")

            await waiting_msg.edit("📤 Uploading...")

            try:
                async with client.action(event.chat_id, 'document'):
                    media = await fast_upload(
                        client=client,
                        file_location=path,
                        reply=None,
                        name=os.path.basename(path),
                        progress_bar_function=None
                    )

                    if format_type == "mp4":
                        video_attr = DocumentAttributeVideo(
                            duration=0, w=0, h=0, supports_streaming=True
                        )
                        mime = "video/mp4"
                        attributes = [video_attr]
                    else:
                        audio_attr = DocumentAttributeAudio(
                            duration=0,
                            title=title,
                            performer="@Socialdownloader1_bot"
                        )
                        mime = "audio/mpeg"
                        attributes = [audio_attr]

                    input_media = InputMediaUploadedDocument(
                        file=await client.upload_file(media),
                        mime_type=mime,
                        attributes=attributes,
                    )

                    await client.send_file(
                        event.chat_id,
                        file=input_media,
                        caption=f"✅ **{title}**\n@Socialdownloader1_bot",
                        force_document=False,
                        supports_streaming=True
                    )

                await waiting_msg.delete()
                await db.set_file_processing_flag(user_id, is_processing=False)

            except Exception as e:
                await db.set_file_processing_flag(user_id, is_processing=False)
                return await event.respond(f"❌ Upload failed.\nReason: {str(e)}")

        else:
            await event.answer("Invalid button data.")
