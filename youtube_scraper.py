"""
YouTube 搜索与字幕抓取模块
- 使用 YouTube Data API v3 搜索视频（需要 API Key）
- 使用 youtube-transcript-api 获取字幕（无需 API Key）
- 使用 yt-dlp 作为备用方案获取视频信息和字幕
"""

import re
import json
import requests
from datetime import datetime

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.formatters import TextFormatter, SRTFormatter
    HAS_TRANSCRIPT_API = True
except ImportError:
    HAS_TRANSCRIPT_API = False

try:
    import yt_dlp
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

from config import YOUTUBE_API_KEY


# === YouTube Data API v3 搜索 ===

def search_videos_api(query, max_results=25, order="relevance", 
                       video_duration="any", published_after=None):
    """
    使用 YouTube Data API v3 搜索视频
    需要 YOUTUBE_API_KEY
    """
    if not YOUTUBE_API_KEY:
        return {"error": "未配置 YouTube API Key。请在 config.py 或环境变量中设置 YOUTUBE_API_KEY。"
                "获取方式: https://console.cloud.google.com/apis/credentials"}

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max_results, 50),
        "order": order,  # relevance, date, viewCount, rating
        "videoDuration": video_duration,  # any, short(<4min), medium(4-20min), long(>20min)
        "key": YOUTUBE_API_KEY,
        "relevanceLanguage": "en",
    }

    if published_after:
        params["publishedAfter"] = published_after

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
        if not video_ids:
            return {"videos": [], "total": 0}

        # 获取详细信息（时长、观看次数等）
        details = get_video_details_api(video_ids)
        details_map = {v["video_id"]: v for v in details}

        videos = []
        for item in data.get("items", []):
            vid = item["id"]["videoId"]
            snippet = item["snippet"]
            detail = details_map.get(vid, {})

            videos.append({
                "video_id": vid,
                "title": snippet.get("title", ""),
                "channel_name": snippet.get("channelTitle", ""),
                "channel_id": snippet.get("channelId", ""),
                "description": snippet.get("description", ""),
                "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "duration": detail.get("duration", ""),
                "language": "en",
                "published_at": snippet.get("publishedAt", ""),
                "view_count": detail.get("view_count", 0),
            })

        total = data.get("pageInfo", {}).get("totalResults", len(videos))
        return {"videos": videos, "total": total}

    except requests.exceptions.RequestException as e:
        return {"error": f"API 请求失败: {str(e)}"}


def get_video_details_api(video_ids):
    """获取视频详细信息（时长、观看次数等）"""
    if not YOUTUBE_API_KEY or not video_ids:
        return []

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "contentDetails,statistics",
        "id": ",".join(video_ids),
        "key": YOUTUBE_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("items", []):
            duration_iso = item.get("contentDetails", {}).get("duration", "")
            results.append({
                "video_id": item["id"],
                "duration": parse_iso_duration(duration_iso),
                "view_count": int(item.get("statistics", {}).get("viewCount", 0)),
            })
        return results
    except Exception:
        return []


def parse_iso_duration(iso_dur):
    """将 ISO 8601 时长 (PT1H2M3S) 转换为可读格式 (1:02:03)"""
    if not iso_dur:
        return ""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_dur)
    if not match:
        return ""
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def get_channel_info_api(channel_id):
    """获取频道详细信息"""
    if not YOUTUBE_API_KEY:
        return None

    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "snippet,statistics",
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        item = items[0]
        return {
            "channel_id": channel_id,
            "name": item["snippet"]["title"],
            "description": item["snippet"].get("description", ""),
            "thumbnail_url": item["snippet"].get("thumbnails", {}).get("medium", {}).get("url", ""),
            "subscriber_count": item["statistics"].get("subscriberCount", "0"),
        }
    except Exception:
        return None


def get_channel_videos_api(channel_id, max_results=25):
    """获取频道最新视频"""
    if not YOUTUBE_API_KEY:
        return {"error": "未配置 YouTube API Key"}

    # 先获取频道的上传播放列表 ID
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "contentDetails",
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return {"error": "未找到频道"}

        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        # 获取播放列表中的视频
        url = "https://www.googleapis.com/youtube/v3/playlistItems"
        params = {
            "part": "snippet",
            "playlistId": uploads_playlist,
            "maxResults": min(max_results, 50),
            "key": YOUTUBE_API_KEY,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        video_ids = [item["snippet"]["resourceId"]["videoId"] for item in data.get("items", [])]
        if not video_ids:
            return {"videos": [], "total": 0}

        details = get_video_details_api(video_ids)
        details_map = {v["video_id"]: v for v in details}

        videos = []
        for item in data.get("items", []):
            snippet = item["snippet"]
            vid = snippet["resourceId"]["videoId"]
            detail = details_map.get(vid, {})
            videos.append({
                "video_id": vid,
                "title": snippet.get("title", ""),
                "channel_name": snippet.get("channelTitle", ""),
                "channel_id": channel_id,
                "description": snippet.get("description", ""),
                "thumbnail_url": snippet.get("thumbnails", {}).get("medium", {}).get("url", ""),
                "duration": detail.get("duration", ""),
                "language": "en",
                "published_at": snippet.get("publishedAt", ""),
                "view_count": detail.get("view_count", 0),
            })

        return {"videos": videos, "total": len(videos)}

    except requests.exceptions.RequestException as e:
        return {"error": f"获取频道视频失败: {str(e)}"}


# === 无 API Key 的备用方案（使用 yt-dlp 搜索） ===

def search_videos_ytdlp(query, max_results=50):
    """
    使用 yt-dlp 搜索视频（无需 API Key）
    注意：速度较慢，但不需要 API Key
    """
    if not HAS_YTDLP:
        return {"error": "未安装 yt-dlp。请运行: pip install yt-dlp"}

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "force_generic_extractor": False,
        "compat_opts": ["no-youtube-prefer-utc"],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # ytsearch 前缀表示搜索
            result = ydl.extract_info(
                f"ytsearch{max_results}:{query}", download=False
            )

        if not result or "entries" not in result:
            return {"videos": [], "total": 0}

        videos = []
        for entry in result["entries"]:
            if not entry:
                continue
            videos.append({
                "video_id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "channel_name": entry.get("channel", entry.get("uploader", "")),
                "channel_id": entry.get("channel_id", ""),
                "description": entry.get("description", "")[:500] if entry.get("description") else "",
                "thumbnail_url": entry.get("thumbnail", ""),
                "duration": format_duration(entry.get("duration")),
                "language": entry.get("language", "en"),
                "published_at": entry.get("upload_date", ""),
                "view_count": entry.get("view_count", 0) or 0,
            })

        return {"videos": videos, "total": len(videos)}

    except Exception as e:
        return {"error": f"yt-dlp 搜索失败: {str(e)}"}


def format_duration(seconds):
    """将秒数转换为可读时长格式"""
    if not seconds:
        return ""
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# === 字幕抓取 ===

def get_transcript(video_id, languages=None):
    """
    获取视频字幕/转录文本
    优先使用 youtube-transcript-api，失败时使用 yt-dlp
    """
    if languages is None:
        languages = ["en", "en-US", "en-GB", "zh-Hans", "zh-Hant", "zh"]

    # 尝试 youtube-transcript-api
    if HAS_TRANSCRIPT_API:
        try:
            ytt_api = YouTubeTranscriptApi()
            transcript = ytt_api.fetch(video_id, languages=languages)
            formatter = TextFormatter()
            text = formatter.format_transcript(transcript)

            # 也生成带时间戳的版本
            timestamped_lines = []
            for snippet in transcript:
                start = snippet.start
                mins = int(start // 60)
                secs = int(start % 60)
                timestamped_lines.append(f"[{mins:02d}:{secs:02d}] {snippet.text}")

            return {
                "success": True,
                "text": text,
                "timestamped": "\n".join(timestamped_lines),
                "source": "youtube-transcript-api",
                "segments": len(transcript),
            }
        except Exception as e:
            # 尝试列出可用字幕
            try:
                ytt_api = YouTubeTranscriptApi()
                transcript_list = ytt_api.list(video_id)
                available = [t.language_code for t in transcript_list]
                return {
                    "success": False,
                    "error": f"获取字幕失败: {str(e)}",
                    "available_languages": available,
                }
            except Exception:
                pass

    # 备用方案：yt-dlp
    if HAS_YTDLP:
        return get_transcript_ytdlp(video_id)

    return {"success": False, "error": "未安装字幕抓取工具。请安装 youtube-transcript-api 或 yt-dlp"}


def get_transcript_ytdlp(video_id):
    """使用 yt-dlp 获取字幕（备用方案）"""
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "zh-Hans", "zh"],
            "skip_download": True,
            "outtmpl": f"/tmp/yt_sub_{video_id}",
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

        # 获取自动字幕
        subtitles = info.get("subtitles", {}) or {}
        auto_subtitles = info.get("automatic_captions", {}) or {}

        # 优先手动字幕，其次自动字幕
        for lang in ["en", "en-US", "en-GB"]:
            if lang in subtitles:
                sub_url = subtitles[lang][0]["url"] if subtitles[lang] else None
                if sub_url:
                    return _fetch_subtitle_text(sub_url, "yt-dlp (manual)")
            if lang in auto_subtitles:
                sub_url = auto_subtitles[lang][0]["url"] if auto_subtitles[lang] else None
                if sub_url:
                    return _fetch_subtitle_text(sub_url, "yt-dlp (auto)")

        available = list(set(list(subtitles.keys()) + list(auto_subtitles.keys())))
        return {
            "success": False,
            "error": "未找到英文字幕",
            "available_languages": available,
        }

    except Exception as e:
        return {"success": False, "error": f"yt-dlp 字幕获取失败: {str(e)}"}


def _fetch_subtitle_text(url, source):
    """从 URL 获取字幕文本"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        # 尝试解析 JSON3 格式
        try:
            data = resp.json()
            lines = []
            for event in data.get("events", []):
                segs = event.get("segs", [])
                text = "".join(seg.get("utf8", "") for seg in segs).strip()
                if text and text != "\n":
                    start_ms = event.get("tStartMs", 0)
                    mins = start_ms // 60000
                    secs = (start_ms % 60000) // 1000
                    lines.append(f"[{mins:02d}:{secs:02d}] {text}")
            return {
                "success": True,
                "text": "\n".join(lines),
                "timestamped": "\n".join(lines),
                "source": source,
                "segments": len(lines),
            }
        except (json.JSONDecodeError, KeyError):
            pass

        # 尝试解析 XML/SRT 格式
        text = resp.text
        # 简单清理 XML 标签
        clean_text = re.sub(r"<[^>]+>", " ", text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return {
            "success": True,
            "text": clean_text,
            "timestamped": clean_text,
            "source": source,
            "segments": 0,
        }
    except Exception as e:
        return {"success": False, "error": f"字幕下载失败: {str(e)}"}


def get_transcript_srt(video_id, languages=None):
    """获取 SRT 格式字幕"""
    if not HAS_TRANSCRIPT_API:
        return None

    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=languages)
        formatter = SRTFormatter()
        return formatter.format_transcript(transcript)
    except Exception:
        return None


# === 统一搜索入口 ===

def search_videos(query, max_results=25, method="auto"):
    """
    统一搜索入口
    method: "api" | "ytdlp" | "auto" (优先 API，失败时回退 yt-dlp)
    """
    if method == "api" or (method == "auto" and YOUTUBE_API_KEY):
        result = search_videos_api(query, max_results=max_results)
        if "error" not in result:
            return result
        if method == "api":
            return result

    if method == "ytdlp" or method == "auto":
        return search_videos_ytdlp(query, max_results=max_results)

    return {"error": "无法搜索：未配置 API Key 且未安装 yt-dlp"}