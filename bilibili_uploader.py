"""
B站自动上传模块
使用 bilibili-api-python 实现视频上传
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

from bilibili_api import video_uploader, Credential

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
FINISHED_DIR = Path(r"D:\AI Related\VideoCaptioner\完成译制")
META_DIR = Path(r"D:\AI Related\VideoCaptioner\译制中")

# ── 标题/简介模板 ──

TITLE_TEMPLATE = "[双语] {cn_title} | {channel}"
DESC_TEMPLATE = """AI烤肉仅供参考，欢迎指正，欢迎补充，欢迎讨论。
原标题：{en_title}
原地址：{url}
原视频作者：{channel}
原作上传时间：{upload_date}
字幕：VideoCaptioner
打轴：VideoCaptioner
翻译：deepseek-v4-flash&mimo-v2.5-pro
校对：Jill"""


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_credential() -> Credential | None:
    """从配置获取 B站凭证"""
    config = load_config()
    cookie_str = config.get("bilibili", {}).get("cookie", "")
    if not cookie_str:
        return None

    # 从 cookie 字符串中提取关键字段
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()

    sessdata = cookies.get("SESSDATA", "")
    bili_jct = cookies.get("bili_jct", "")
    buvid3 = cookies.get("buvid3", "")
    dedeuserid = cookies.get("DedeUserID", "")

    if not sessdata or not bili_jct:
        return None

    return Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3,
        dedeuserid=dedeuserid,
    )


def get_video_meta(video_filename: str) -> dict:
    """查找视频对应的 meta.json"""
    safe_name = Path(video_filename).stem
    if META_DIR.exists():
        for meta_file in META_DIR.glob("*.meta.json"):
            meta_title = meta_file.stem.replace(".meta", "")
            if meta_title and (meta_title in safe_name or safe_name in meta_title):
                with open(meta_file, "r", encoding="utf-8") as f:
                    return json.load(f)
    return {}


def get_upload_date(youtube_url: str) -> str:
    """用 yt-dlp 获取视频上传时间"""
    if not youtube_url:
        return "未知"
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-download", "--print", "%(upload_date)s", youtube_url],
            capture_output=True, text=True, check=False, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            date_str = result.stdout.strip()
            if len(date_str) == 8:
                return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            return date_str
    except:
        pass
    return "未知"


def generate_title_desc(cn_title: str, meta: dict) -> tuple[str, str]:
    """生成标题和简介"""
    channel = meta.get("channel", "未知作者")
    en_title = meta.get("title", "")
    url = meta.get("url", "")
    upload_date = get_upload_date(url) if url else "未知"

    title = TITLE_TEMPLATE.format(cn_title=cn_title, channel=channel)
    desc = DESC_TEMPLATE.format(
        en_title=en_title,
        url=url,
        channel=channel,
        upload_date=upload_date,
    )
    return title, desc


def scan_finished_videos() -> list[Path]:
    """扫描完成译制目录，返回 mp4 文件列表（按时间倒序）"""
    if not FINISHED_DIR.exists():
        return []
    files = list(FINISHED_DIR.glob("*.mp4"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


async def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] = None,
    credential: Credential = None,
    log_fn=None,
    only_self: bool = False,
) -> dict:
    """上传视频到B站"""
    if log_fn is None:
        log_fn = print

    if credential is None:
        credential = get_credential()
    if credential is None:
        return {"success": False, "bvid": "", "message": "Cookie 未配置"}

    if tags is None:
        tags = ["字幕", "翻译", "双语", "AI翻译"]

    log_fn(f"准备上传: {title}")
    log_fn(f"文件: {video_path}")

    try:
        page = video_uploader.VideoUploaderPage(
            path=video_path,
            title=title,
            description=description,
        )

        from bilibili_api.utils.picture import Picture
        import tempfile
        from PIL import Image
        tmp_cover = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img = Image.new("RGB", (1, 1), (0, 0, 0))
        img.save(tmp_cover.name, "JPEG")
        tmp_cover.close()
        cover_path = tmp_cover.name
        log_fn("已生成默认封面")

        from bilibili_api.video_uploader import VideoMeta
        meta = VideoMeta(
            tid=36,
            title=title,
            desc=description,
            tag=",".join(tags),
            copyright=2,
            source="",
        )
        meta.desc_format_id = 9999
        meta.dynamic = ""
        meta.interactive = 0
        meta.act_reserve_create = 0
        meta.no_disturbance = 0
        meta.no_reprint = 1
        meta.web_os = 1
        meta.recreate = -1
        meta.dolby = 0
        meta.lossless_music = 0
        meta.subtitle = {"open": 0, "lan": ""}

        if only_self:
            import datetime
            dtime = int((datetime.datetime.now() + datetime.timedelta(days=365*10)).timestamp())
            meta["dtime"] = dtime
            log_fn("设置为仅自己可见（延迟发布10年）")

        uploader = video_uploader.VideoUploader(
            pages=[page],
            meta=meta,
            credential=credential,
            cover=cover_path,
        )

        result = await uploader.start()

        if result:
            bvid = result.get("bvid", "")
            log_fn(f"上传成功！BV号: {bvid}")
            return {"success": True, "bvid": bvid, "message": f"上传成功: {bvid}"}
        else:
            log_fn("上传失败: 未知错误")
            return {"success": False, "bvid": "", "message": "上传失败"}

    except Exception as e:
        log_fn(f"上传出错: {e}")
        return {"success": False, "bvid": "", "message": str(e)}
