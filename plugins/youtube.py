import aiohttp
import asyncio
import os
import re
import hashlib
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

# yt-dlp wrapper from your utils
from utils import YoutubeDL, InputMediaPhotoExternal, db
from utils import InputMediaUploadedDocument, DocumentAttributeVideo, fast_upload
from utils import DocumentAttributeAudio, WebpageMediaEmptyError
from run import Button, Buttons

# Correct import as you requested
from py_yt import VideosSearch


# Optionally create a shared executor for blocking calls
_executor = ThreadPoolExecutor(max_workers=4)


class YoutubeDownloader:

    @classmethod
    def initialize(cls):
        cls.MAXIMUM_DOWNLOAD_SIZE_MB = 100
        cls.DOWNLOAD_DIR = 'repository/Youtube'
        cls.COOKIES_PATH = 'resources/cookies.txt'  # path used by yt-dlp fallback

        if not os.path.isdir(cls.DOWNLOAD_DIR):
            os.makedirs(cls.DOWNLOAD_DIR, exist_ok=True)

    @staticmethod
    @lru_cache(maxsize=128)
    def get_file_path(url, format_id, extension):
        key = url + format_id + extension
        url_hash = hashlib.blake2b(key.encode()).hexdigest()
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
            if re.match(pattern, url):
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

    # --------------------------- Info fetching ------------------------------

    @staticmethod
    def _videossearch_blocking(query):
        """
        Blocking wrapper for VideosSearch.result() — runs in thread executor.
        Returns results dict or raises.
        """
        vs = VideosSearch(query, limit=1)
        return vs.result()

    @staticmethod
    async def fetch_video_info(url):
        """
        Primary: use py_yt.VideosSearch to fetch metadata (title, thumbnail, id).
        Fallback: use yt-dlp with cookies (if available).
        Returns: dict with keys: video_id, title, thumbnail
        """
        # normalize id
        video_id = (url.split("?si=")[0]
                    .replace("https://www.youtube.com/watch?v=", "")
                    .replace("https://www.youtube.com/shorts/", "")
                    .split("&")[0])

        # 1) Try py_yt (VideosSearch) in executor to avoid blocking event loop
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(_executor, YoutubeDownloader._videossearch_blocking, video_id)
            # py_yt structure: {'result': [{'id':..., 'title':..., 'thumbnails': [{'url':...}], ...}], 'total': ...}
            if isinstance(result, dict):
                rlist = result.get('result') or result.get('videos') or []
                if rlist:
                    first = rlist[0]
                    title = first.get('title') or first.get('name') or f'YouTube Video {video_id}'
                    vid = first.get('id') or video_id
                    # thumbnails can be a list with dicts
                    thumbs = first.get('thumbnails') or first.get('thumbnail') or []
                    thumb_url = None
                    if isinstance(thumbs, list) and len(thumbs) > 0:
                        # choose the first available thumbnail url
                        t0 = thumbs[0]
                        if isinstance(t0, dict):
                            thumb_url = t0.get('url') or t0.get('thumbnail')
                        elif isinstance(t0, str):
                            thumb_url = t0
                    elif isinstance(thumbs, str):
                        thumb_url = thumbs

                    return {
                        'video_id': vid,
                        'title': title,
                        'thumbnail': thumb_url
                    }
        except Exception:
            # primary method failed — we'll fallback to yt-dlp below
            pass

        # 2) Fallback: use yt-dlp (with cookies support if available)
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
            }
            # include cookiefile if exists
            if os.path.isfile(YoutubeDownloader.COOKIES_PATH):
                ydl_opts['cookiefile'] = YoutubeDownloader.COOKIES_PATH

            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title') or f'YouTube Video {video_id}'
                thumbnail = info.get('thumbnail')
                vid = info.get('id') or video_id
                return {
                    'video_id': vid,
                    'title': title,
                    'thumbnail': thumbnail
                }
        except Exception as e:
            # As a last resort return best-effort minimal info
            return {
                'video_id': video_id,
                'title': f'YouTube Video ({video_id})',
                'thumbnail': None
            }

    # --------------------------- Formats (yt-dlp) ---------------------------

    @staticmethod
    def _get_formats(url):
        ydl_opts = {
            'listformats': True,
            'no_warnings': True,
            'quiet': True,
        }
        # include cookiefile if available
        if os.path.isfile(YoutubeDownloader.COOKIES_PATH):
            ydl_opts['cookiefile'] = YoutubeDownloader.COOKIES_PATH

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = info.get('formats', []) if info else []
        return formats

    # --------------------------- UI / Bot flows ------------------------------

    @staticmethod
    async def send_youtube_info(client, event, youtube_link):
        """
        Called when a youtube link is detected; sends metadata + buttons.
        Uses py_yt first; falls back to yt-dlp if needed.
        """
        info = await YoutubeDownloader.fetch_video_info(youtube_link)
        video_id = info.get('video_id')
        thumbnail_url = info.get('thumbnail')
        title = info.get('title', 'Unknown Title')

        # API-based formats buttons (keeps your previous format)
        video_buttons = [
            [Button.inline("🎬 MP4 (Video)", data=f"ytapi/{video_id}/mp4")]
        ]
        audio_buttons = [
            [Button.inline("🎧 MP3 (Audio)", data=f"ytapi/{video_id}/mp3")]
        ]

        buttons = video_buttons + audio_buttons
        buttons.append(Buttons.cancel_button)

        # send thumbnail if available, otherwise simple text
        if thumbnail_url:
            try:
                thumbnail = InputMediaPhotoExternal(thumbnail_url)
                thumbnail.ttl_seconds = 0
                await client.send_file(
                    event.chat_id,
                    file=thumbnail,
                    caption=f"🎵 **{title}**\nSelect a format to download:",
                    buttons=buttons
                )
                return
            except WebpageMediaEmptyError:
                # fallthrough to text send
                pass
            except Exception:
                # non-fatal — keep going to text reply
                pass

        # fallback: send plain message with buttons
        await event.respond(f"🎵 **{title}**\nSelect a format to download:", buttons=buttons)

    @staticmethod
    async def download_and_send_yt_file(client, event):
        """
        Handles button presses like ytapi/<video_id>/<mp3|mp4>
        Keeps your worker API download flow unchanged.
        """
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
