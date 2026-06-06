"""
B站自动上传模块
基于 bilibili-api-python (nemo2011/bilibili-api) 实现视频上传
"""
import json
import os
import asyncio
import subprocess
import re
from pathlib import Path

from bilibili_api import Credential
from bilibili_api.video_uploader import (
    VideoUploader, VideoUploaderPage, VideoMeta,
    VideoUploaderEvents
)
from bilibili_api.utils.picture import Picture

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

# ── 标题/简介模板 ──

TITLE_TEMPLATE = "[双语] {cn_title} | {channel}"
DESC_TEMPLATE = """AI烤肉仅供参考，欢迎指正，欢迎补充，欢迎讨论。
原标题：{en_title}
原地址：{url}
原视频作者：{channel}
原作上传时间：{upload_date}
字幕：VideoCaptioner
打轴：VideoCaptioner
翻译：AI翻译
校对：Jill"""


def load_config():
    if not CONFIG_PATH.exists():
        return {}
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
    except Exception:
        pass
    return "未知"


def generate_title_desc(cn_title: str, channel: str, en_title: str = "",
                         youtube_url: str = "") -> tuple[str, str]:
    """生成 B站标题和简介"""
    title = TITLE_TEMPLATE.format(cn_title=cn_title, channel=channel)
    upload_date = get_upload_date(youtube_url) if youtube_url else "未知"
    desc = DESC_TEMPLATE.format(
        en_title=en_title,
        url=youtube_url or "",
        channel=channel,
        upload_date=upload_date,
    )
    return title, desc


async def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] = None,
    tid: int = 36,
    source_url: str = "",
    cover_path: str = "",
    cover_url: str = "",
    credential: Credential = None,
    only_self: bool = False,
    progress_callback=None,
) -> dict:
    """
    上传视频到 B站

    Args:
        video_path: 视频文件路径
        title: 视频标题
        description: 视频简介
        tags: 标签列表
        tid: 分区 ID (默认 36 = 知识 > 社科·法律·心理)
        source_url: 转载来源 URL
        cover_path: 封面图片本地路径
        cover_url: 封面图片 URL
        credential: B站凭证
        only_self: 仅自己可见（延迟发布10年）
        progress_callback: 进度回调 fn(percentage, message)

    Returns:
        dict: {"success": bool, "bvid": str, "message": str}
    """
    def log(msg):
        if progress_callback:
            progress_callback(0, msg)

    if credential is None:
        credential = get_credential()
    if credential is None:
        return {"success": False, "bvid": "", "message": "B站 Cookie 未配置，请在 config.json 中设置 bilibili.cookie"}

    if not os.path.exists(video_path):
        return {"success": False, "bvid": "", "message": f"视频文件不存在: {video_path}"}

    if tags is None:
        tags = ["字幕", "翻译", "双语", "AI翻译"]

    log(f"准备上传: {title}")

    try:
        # 创建分P
        page = VideoUploaderPage(
            path=video_path,
            title=title,
            description=description[:200] if description else "",
        )

        # 加载封面
        cover = None
        if cover_url:
            try:
                cover = await Picture.load_url(cover_url)
                log("封面已从 URL 加载")
            except Exception as e:
                log(f"URL 封面加载失败: {e}")

        if cover is None and cover_path and os.path.exists(cover_path):
            try:
                cover = Picture().from_file(cover_path)
                log("封面已从本地文件加载")
            except Exception as e:
                log(f"本地封面加载失败: {e}")

        # 构建元数据
        meta = VideoMeta(
            tid=tid,
            title=title[:80],  # B站标题限制80字
            desc=description[:2000] if description else "",  # B站简介限制2000字
            tags=tags,
            cover=cover,
            source=source_url,  # 转载来源
            copyright=2,  # 2=转载
            no_reprint=True,
        )

        # 仅自己可见：延迟发布10年
        if only_self:
            import datetime
            dtime = int((datetime.datetime.now() + datetime.timedelta(days=365*10)).timestamp())
            meta.delay_time = dtime
            log("设置为仅自己可见（延迟发布10年）")

        # 创建上传器
        uploader = VideoUploader(
            pages=[page],
            meta=meta,
            credential=credential,
        )

        # 监听上传进度
        @uploader.on(VideoUploaderEvents.PRE_PAGE)
        async def on_pre_page(data):
            log(f"开始上传第 {data['page']} P...")

        @uploader.on(VideoUploaderEvents.AFTER_CHUNK)
        async def on_chunk(data):
            pct = data['offset'] / data['total'] * 100
            if progress_callback:
                progress_callback(pct, f"上传进度: {pct:.1f}%")

        @uploader.on(VideoUploaderEvents.COMPLETED)
        async def on_done(data):
            log(f"上传完成！BV号: {data.get('bvid', '未知')}")

        # 开始上传
        log("正在上传，请耐心等待...")
        result = await uploader.start()

        if result:
            bvid = result.get("bvid", "")
            log(f"上传成功！BV号: {bvid}")
            return {"success": True, "bvid": bvid, "message": f"上传成功: https://www.bilibili.com/video/{bvid}"}
        else:
            return {"success": False, "bvid": "", "message": "上传失败: 返回结果为空"}

    except Exception as e:
        error_msg = str(e)
        if "login" in error_msg.lower() or "credential" in error_msg.lower() or "sessdata" in error_msg.lower():
            return {"success": False, "bvid": "", "message": f"认证失败，请检查 Cookie 是否正确: {error_msg[:200]}"}
        return {"success": False, "bvid": "", "message": f"上传出错: {error_msg[:300]}"}


def get_available_tids() -> dict:
    """获取常用 B站分区 ID"""
    return {
        "知识 > 社科·法律·心理": 36,
        "知识 > 人文历史": 228,
        "知识 > 社科·法律·心理 > 社会学": 36,
        "知识 > 科学科普": 200,
        "知识 > 校园学习": 229,
        "生活 > 日常": 21,
        "生活 > 其他": 160,
        "动画 > 综合": 184,
        "影视 > 影视杂谈": 182,
        "影视 > 纪录片": 185,
        "人文社科": 124,
        "综合": 160,
    }