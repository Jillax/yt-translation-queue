"""
YT Translation Queue - YouTube 视频翻译待译库
Flask Web 应用主文件
"""

import os
import json
import webbrowser
import threading
from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, flash, send_file, Response
)

from config import (
    APP_HOST, APP_PORT, DEBUG, TRANSLATION_STATUSES,
    DEFAULT_SEARCH_KEYWORDS, SUBTITLES_DIR
)
from database import (
    init_db, add_video, get_videos, get_video, update_video_status,
    update_video_notes, update_video_subtitle, delete_video,
    get_dashboard_stats, add_tag, get_all_tags, delete_tag,
    add_video_tag, remove_video_tag, add_channel, get_channels,
    delete_channel, update_channel_last_checked, add_search_history,
    get_search_history
)
from youtube_scraper import (
    search_videos, get_transcript, get_transcript_srt,
    get_channel_info_api, get_channel_videos_api
)
from llm_segment import segment_transcript, simple_segment
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_CHARS_PER_LINE
import subprocess
import json as json_lib
import asyncio

# B站上传模块（可选依赖）
try:
    from bilibili_uploader import upload_video as bilibili_upload, generate_title_desc, get_credential
    HAS_BILIBILI = True
except ImportError:
    HAS_BILIBILI = False
    def bilibili_upload(**kwargs):
        return {"success": False, "bvid": "", "message": "bilibili-api-python 未安装，请运行: pip install bilibili-api-python"}
    def generate_title_desc(cn_title, channel, en_title="", youtube_url=""):
        return f"[双语] {cn_title} | {channel}", ""
    def get_credential():
        return None

app = Flask(__name__)
app.secret_key = "yt-translation-queue-secret-key"

# 初始化数据库
init_db()


# === 辅助函数 ===

def format_datetime(iso_str):
    """格式化 ISO 日期为友好显示"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return iso_str[:16] if len(iso_str) > 16 else iso_str


def format_number(num):
    """格式化数字为友好显示"""
    if not num:
        return "0"
    num = int(num)
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(num)


# 注册模板过滤器
app.jinja_env.filters["format_datetime"] = format_datetime
app.jinja_env.filters["format_number"] = format_number


# === 页面路由 ===

@app.route("/")
def index():
    """首页 - 视频列表"""
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status", "")
    tag_id = request.args.get("tag_id", "", type=str)
    channel_id = request.args.get("channel_id", "")
    search = request.args.get("search", "")
    sort_by = request.args.get("sort_by", "added_at")
    sort_order = request.args.get("sort_order", "DESC")

    tag_id_int = int(tag_id) if tag_id and tag_id.isdigit() else None

    result = get_videos(
        status=status if status else None,
        tag_id=tag_id_int,
        channel_id=channel_id if channel_id else None,
        search=search if search else None,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        per_page=20,
    )

    tags = get_all_tags()
    channels = get_channels()

    return render_template(
        "index.html",
        videos=result["videos"],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        statuses=TRANSLATION_STATUSES,
        tags=tags,
        channels=channels,
        current_status=status,
        current_tag_id=tag_id,
        current_channel_id=channel_id,
        current_search=search,
        current_sort_by=sort_by,
        current_sort_order=sort_order,
    )


@app.route("/search")
def search_page():
    """搜索页面"""
    query = request.args.get("q", "")
    results = []
    error = None

    if query:
        result = search_videos(query, max_results=50)
        if "error" in result:
            error = result["error"]
        else:
            results = result.get("videos", [])
            add_search_history(query, len(results))

    recent_searches = get_search_history(limit=10)

    return render_template(
        "search.html",
        query=query,
        results=results,
        error=error,
        recent_searches=recent_searches,
        keywords=DEFAULT_SEARCH_KEYWORDS,
    )


@app.route("/video/<int:video_db_id>")
def video_detail(video_db_id):
    """视频详情页面"""
    video = get_video(video_db_id)
    if not video:
        flash("视频不存在", "error")
        return redirect(url_for("index"))

    tags = get_all_tags()
    video_tag_names = video.get("tags_list", [])
    video_tag_ids = []
    for tag in tags:
        if tag["name"] in video_tag_names:
            video_tag_ids.append(tag["id"])

    return render_template(
        "video_detail.html",
        video=video,
        statuses=TRANSLATION_STATUSES,
        tags=tags,
        video_tag_ids=video_tag_ids,
    )


@app.route("/channels")
def channels_page():
    """频道管理页面"""
    channels = get_channels()
    return render_template("channels.html", channels=channels)


@app.route("/dashboard")
def dashboard():
    """数据看板"""
    stats = get_dashboard_stats()
    return render_template("dashboard.html", stats=stats, statuses=TRANSLATION_STATUSES)


@app.route("/translate")
def translate_page():
    """翻译管理页面"""
    videos = get_videos(status="pending", per_page=50)
    translating = get_videos(status="translating", per_page=50)
    translated = get_videos(status="translated", per_page=50)
    return render_template(
        "translate.html",
        pending_videos=videos["videos"],
        translating_videos=translating["videos"],
        translated_videos=translated["videos"],
        statuses=TRANSLATION_STATUSES,
    )


# === API 路由 ===

@app.route("/api/search", methods=["POST"])
def api_search():
    """搜索 YouTube 视频"""
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "请输入搜索关键词"}), 400

    result = search_videos(query, max_results=data.get("max_results", 25))
    if "error" not in result:
        add_search_history(query, result.get("total", 0))
    return jsonify(result)


@app.route("/api/video/add", methods=["POST"])
def api_add_video():
    """添加视频到待译库"""
    data = request.get_json() or {}
    if "video_id" not in data:
        return jsonify({"error": "缺少 video_id"}), 400

    try:
        video_db_id, is_new = add_video(data)
        return jsonify({
            "success": True,
            "video_db_id": video_db_id,
            "is_new": is_new,
            "message": "已添加到待译库" if is_new else "视频已在库中",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/video/batch-add", methods=["POST"])
def api_batch_add():
    """批量添加视频"""
    data = request.get_json() or {}
    videos = data.get("videos", [])
    if not videos:
        return jsonify({"error": "没有要添加的视频"}), 400

    added = 0
    skipped = 0
    errors = []
    for v in videos:
        try:
            _, is_new = add_video(v)
            if is_new:
                added += 1
            else:
                skipped += 1
        except Exception as e:
            errors.append(f"{v.get('title', 'unknown')}: {str(e)}")

    return jsonify({
        "success": True,
        "added": added,
        "skipped": skipped,
        "errors": errors,
    })


@app.route("/api/video/<int:video_db_id>/status", methods=["POST"])
def api_update_status(video_db_id):
    """更新翻译状态"""
    data = request.get_json() or {}
    status = data.get("status", "")
    valid_statuses = [s[0] for s in TRANSLATION_STATUSES]
    if status not in valid_statuses:
        return jsonify({"error": f"无效状态，可选: {', '.join(valid_statuses)}"}), 400

    update_video_status(video_db_id, status)
    return jsonify({"success": True})


@app.route("/api/video/<int:video_db_id>/notes", methods=["POST"])
def api_update_notes(video_db_id):
    """更新备注"""
    data = request.get_json() or {}
    notes = data.get("notes", "")
    update_video_notes(video_db_id, notes)
    return jsonify({"success": True})


@app.route("/api/video/<int:video_db_id>/transcript", methods=["POST"])
def api_get_transcript(video_db_id):
    """获取视频字幕"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    result = get_transcript(video["video_id"])
    if result.get("success"):
        # 保存到数据库
        update_video_subtitle(video_db_id, result["timestamped"])
    return jsonify(result)


@app.route("/api/video/<int:video_db_id>/segment", methods=["POST"])
def api_segment_transcript(video_db_id):
    """LLM 智能断句"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    if not video.get("subtitle_text"):
        return jsonify({"error": "请先获取字幕再进行断句"}), 400

    data = request.get_json() or {}
    max_chars = data.get("max_chars", LLM_MAX_CHARS_PER_LINE)
    use_llm = data.get("use_llm", True)
    api_key = data.get("api_key", "").strip() or None
    base_url = data.get("base_url", "").strip() or None
    model = data.get("model", "").strip() or None

    if use_llm:
        result = segment_transcript(
            video["subtitle_text"],
            max_chars=max_chars,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
    else:
        result = {
            "success": True,
            "segmented": simple_segment(video["subtitle_text"], max_chars=max_chars),
            "lines_count": 0,
            "max_chars": max_chars,
            "model": "simple",
        }

    return jsonify(result)


@app.route("/api/video/<int:video_db_id>/segment/save", methods=["POST"])
def api_save_segmented(video_db_id):
    """保存断句后的字幕"""
    data = request.get_json() or {}
    segmented_text = data.get("text", "")
    if not segmented_text:
        return jsonify({"error": "没有要保存的内容"}), 400

    update_video_subtitle(video_db_id, segmented_text)
    return jsonify({"success": True})


@app.route("/api/video/<int:video_db_id>/segment/export")
def api_export_segmented(video_db_id):
    """导出断句后的字幕为文本文件"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    if not video.get("subtitle_text"):
        return jsonify({"error": "没有字幕内容"}), 404

    filename = f"{video['video_id']}_segmented.txt"
    filename = "".join(c for c in filename if c.isalnum() or c in "._- ")

    return Response(
        video["subtitle_text"],
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@app.route("/api/video/<int:video_db_id>/transcript/srt")
def api_get_transcript_srt(video_db_id):
    """下载 SRT 字幕文件"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    srt_content = get_transcript_srt(video["video_id"])
    if not srt_content:
        return jsonify({"error": "无法获取 SRT 字幕"}), 404

    filename = f"{video['video_id']}_{video['title'][:50]}.srt"
    filename = "".join(c for c in filename if c.isalnum() or c in "._- ")

    return Response(
        srt_content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename={filename}"},
    )


@app.route("/api/video/<int:video_db_id>", methods=["DELETE"])
def api_delete_video(video_db_id):
    """删除视频"""
    delete_video(video_db_id)
    return jsonify({"success": True})


@app.route("/api/video/<int:video_db_id>/tags", methods=["POST"])
def api_add_tag_to_video(video_db_id):
    """给视频添加标签"""
    data = request.get_json() or {}
    tag_id = data.get("tag_id")
    if not tag_id:
        return jsonify({"error": "缺少 tag_id"}), 400

    add_video_tag(video_db_id, tag_id)
    return jsonify({"success": True})


@app.route("/api/video/<int:video_db_id>/tags/<int:tag_id>", methods=["DELETE"])
def api_remove_tag_from_video(video_db_id, tag_id):
    """移除视频标签"""
    remove_video_tag(video_db_id, tag_id)
    return jsonify({"success": True})


# === 标签 API ===

@app.route("/api/tags", methods=["GET"])
def api_get_tags():
    """获取所有标签"""
    return jsonify(get_all_tags())


@app.route("/api/tags", methods=["POST"])
def api_create_tag():
    """创建标签"""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    color = data.get("color", "#007bff")
    if not name:
        return jsonify({"error": "标签名不能为空"}), 400

    tag_id = add_tag(name, color)
    if tag_id:
        return jsonify({"success": True, "tag_id": tag_id})
    return jsonify({"error": "标签已存在"}), 400


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id):
    """删除标签"""
    delete_tag(tag_id)
    return jsonify({"success": True})


# === 频道 API ===

@app.route("/api/channels", methods=["POST"])
def api_add_channel():
    """添加关注频道"""
    data = request.get_json() or {}
    channel_id = data.get("channel_id", "").strip()
    if not channel_id:
        return jsonify({"error": "请输入频道 ID"}), 400

    # 尝试获取频道信息
    info = get_channel_info_api(channel_id)
    if info:
        success = add_channel(info)
    else:
        # 如果 API 不可用，手动添加
        success = add_channel({
            "channel_id": channel_id,
            "name": data.get("name", channel_id),
        })

    if success:
        return jsonify({"success": True, "message": "频道已添加"})
    return jsonify({"error": "频道已存在"}), 400


@app.route("/api/channels/<int:channel_db_id>", methods=["DELETE"])
def api_delete_channel(channel_db_id):
    """删除关注频道"""
    delete_channel(channel_db_id)
    return jsonify({"success": True})


@app.route("/api/channels/<string:channel_id>/fetch", methods=["POST"])
def api_fetch_channel_videos(channel_id):
    """获取频道最新视频并入库"""
    result = get_channel_videos_api(channel_id)
    if "error" in result:
        return jsonify(result), 400

    added = 0
    skipped = 0
    for v in result.get("videos", []):
        _, is_new = add_video(v)
        if is_new:
            added += 1
        else:
            skipped += 1

    update_channel_last_checked(channel_id)

    return jsonify({
        "success": True,
        "added": added,
        "skipped": skipped,
        "total_found": result.get("total", 0),
    })


# === 视频下载 API ===

@app.route("/api/video/<int:video_db_id>/download", methods=["POST"])
def api_download_video(video_db_id):
    """下载视频（yt-dlp 1080p）"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    youtube_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    os.makedirs(output_dir, exist_ok=True)

    safe_title = "".join(c for c in (video.get("title", "") or video["video_id"])[:60] if c.isalnum() or c in "._- ")
    output_template = os.path.join(output_dir, f"{safe_title}.%(ext)s")

    cmd = [
        "yt-dlp", "--no-playlist", "--js-runtimes", "nodejs",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        youtube_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode == 0:
            # Find the downloaded file
            downloaded_files = [f for f in os.listdir(output_dir) if f.startswith(safe_title)]
            return jsonify({
                "success": True,
                "message": f"下载完成: {', '.join(downloaded_files)}",
                "path": output_dir,
                "files": downloaded_files,
            })
        else:
            return jsonify({"error": f"下载失败: {result.stderr[:500]}"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "下载超时（10分钟）"}), 500
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp 未安装，请运行: pip install yt-dlp"}), 500


@app.route("/api/video/<int:video_db_id>/download-thumbnail", methods=["POST"])
def api_download_thumbnail(video_db_id):
    """下载视频封面（最高分辨率）"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    youtube_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads", "thumbnails")
    os.makedirs(output_dir, exist_ok=True)

    safe_title = "".join(c for c in (video.get("title", "") or video["video_id"])[:60] if c.isalnum() or c in "._- ")
    output_template = os.path.join(output_dir, f"{safe_title}.%(ext)s")

    cmd = [
        "yt-dlp", "--no-playlist", "--js-runtimes", "nodejs",
        "--write-thumbnail", "--skip-download",
        "--convert-thumbnails", "jpg",
        "-o", output_template,
        youtube_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            downloaded_files = [f for f in os.listdir(output_dir) if f.startswith(safe_title)]
            return jsonify({
                "success": True,
                "message": f"封面下载完成: {', '.join(downloaded_files)}",
                "path": output_dir,
                "files": downloaded_files,
            })
        else:
            return jsonify({"error": f"封面下载失败: {result.stderr[:300]}"}), 500
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp 未安装"}), 500


@app.route("/api/video/<int:video_db_id>/download-subtitle", methods=["POST"])
def api_download_subtitle(video_db_id):
    """获取 ASS 字幕文件信息"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    youtube_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads", "subtitles")
    os.makedirs(output_dir, exist_ok=True)

    safe_title = "".join(c for c in (video.get("title", "") or video["video_id"])[:60] if c.isalnum() or c in "._- ")
    output_template = os.path.join(output_dir, f"{safe_title}.%(ext)s")

    # List available subtitles
    cmd_list = ["yt-dlp", "--no-playlist", "--js-runtimes", "nodejs", "--list-subs", youtube_url]
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=30)
        available_subs = result.stdout if result.returncode == 0 else ""
    except Exception:
        available_subs = ""

    # Download auto-generated subtitles
    cmd = [
        "yt-dlp", "--no-playlist", "--js-runtimes", "nodejs",
        "--write-auto-sub", "--sub-lang", "en",
        "--sub-format", "ass/srt/best",
        "--skip-download",
        "-o", output_template,
        youtube_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            downloaded_files = [f for f in os.listdir(output_dir) if f.startswith(safe_title)]
            return jsonify({
                "success": True,
                "message": f"字幕下载完成: {', '.join(downloaded_files)}",
                "path": output_dir,
                "files": downloaded_files,
                "available": available_subs[:1000],
            })
        else:
            return jsonify({"error": f"字幕下载失败: {result.stderr[:300]}"}), 500
    except FileNotFoundError:
        return jsonify({"error": "yt-dlp 未安装"}), 500


# === B站上传 API ===

@app.route("/api/video/<int:video_db_id>/upload-bilibili", methods=["POST"])
def api_upload_bilibili(video_db_id):
    """上传视频到 B站"""
    video = get_video(video_db_id)
    if not video:
        return jsonify({"error": "视频不存在"}), 404

    data = request.get_json() or {}
    video_path = data.get("video_path", "")
    cover_path = data.get("cover_path", "")
    cover_url = data.get("cover_url", video.get("thumbnail_url", ""))
    cn_title = data.get("title", video.get("title", ""))
    tid = data.get("tid", 36)
    only_self = data.get("only_self", False)
    custom_tags = data.get("tags", "")

    if not video_path:
        return jsonify({"error": "请指定视频文件路径"}), 400

    # 生成标题和简介
    youtube_url = f"https://www.youtube.com/watch?v={video['video_id']}"
    title, desc = generate_title_desc(
        cn_title=cn_title,
        channel=video.get("channel_name", ""),
        en_title=video.get("title", ""),
        youtube_url=youtube_url,
    )

    tags = [t.strip() for t in custom_tags.split(",") if t.strip()] if custom_tags else None

    try:
        result = asyncio.run(bilibili_upload(
            video_path=video_path,
            title=title,
            description=desc,
            tags=tags,
            tid=tid,
            source_url=youtube_url,
            cover_path=cover_path,
            cover_url=cover_url,
            only_self=only_self,
        ))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"上传失败: {str(e)[:300]}"}), 500


@app.route("/api/bilibili/check-credential")
def api_check_bilibili_credential():
    """检查 B站凭证是否配置"""
    cred = get_credential()
    return jsonify({"configured": cred is not None})


# === 统计 API ===

@app.route("/api/dashboard")
def api_dashboard():
    """获取看板数据"""
    return jsonify(get_dashboard_stats())


# === 启动 ===

def open_browser():
    """延迟打开浏览器"""
    webbrowser.open(f"http://{APP_HOST}:{APP_PORT}")


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  YT Translation Queue - YouTube 翻译待译库")
    print(f"  访问地址: http://{APP_HOST}:{APP_PORT}")
    print(f"{'='*50}\n")

    # 自动打开浏览器
    threading.Timer(1.5, open_browser).start()

    app.run(host=APP_HOST, port=APP_PORT, debug=DEBUG)
