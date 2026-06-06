import sqlite3
import os
from datetime import datetime
from config import DATABASE_PATH


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_connection()
    cursor = conn.cursor()

    # 视频表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            channel_name TEXT,
            channel_id TEXT,
            description TEXT,
            thumbnail_url TEXT,
            duration TEXT,
            language TEXT DEFAULT 'en',
            published_at TEXT,
            view_count INTEGER DEFAULT 0,
            translation_status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            notes TEXT,
            subtitle_text TEXT,
            subtitle_downloaded INTEGER DEFAULT 0,
            added_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # 标签表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT DEFAULT '#007bff'
        )
    """)

    # 视频-标签关联表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_tags (
            video_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (video_id, tag_id),
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)

    # 频道表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            thumbnail_url TEXT,
            subscriber_count TEXT,
            video_count INTEGER DEFAULT 0,
            added_at TEXT NOT NULL,
            last_checked TEXT
        )
    """)

    # 搜索历史表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            results_count INTEGER DEFAULT 0,
            searched_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# === 视频操作 ===

def add_video(video_data):
    """添加视频到数据库，返回 (video_id, is_new)"""
    conn = get_connection()
    now = datetime.now().isoformat()
    try:
        cursor = conn.execute(
            "SELECT id FROM videos WHERE video_id = ?", (video_data["video_id"],)
        )
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return existing["id"], False

        cursor = conn.execute(
            """INSERT INTO videos 
            (video_id, title, channel_name, channel_id, description, 
             thumbnail_url, duration, language, published_at, view_count,
             translation_status, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                video_data["video_id"],
                video_data.get("title", ""),
                video_data.get("channel_name", ""),
                video_data.get("channel_id", ""),
                video_data.get("description", ""),
                video_data.get("thumbnail_url", ""),
                video_data.get("duration", ""),
                video_data.get("language", "en"),
                video_data.get("published_at", ""),
                video_data.get("view_count", 0),
                now,
                now,
            ),
        )
        conn.commit()
        video_row_id = cursor.lastrowid
        conn.close()
        return video_row_id, True
    except Exception as e:
        conn.close()
        raise e


def get_videos(status=None, tag_id=None, channel_id=None, search=None,
               sort_by="added_at", sort_order="DESC", page=1, per_page=20):
    """获取视频列表（带筛选、排序、分页）"""
    conn = get_connection()

    where_clauses = []
    params = []

    if status:
        where_clauses.append("v.translation_status = ?")
        params.append(status)

    if channel_id:
        where_clauses.append("v.channel_id = ?")
        params.append(channel_id)

    if search:
        where_clauses.append("(v.title LIKE ? OR v.channel_name LIKE ? OR v.description LIKE ?)")
        search_term = f"%{search}%"
        params.extend([search_term, search_term, search_term])

    if tag_id:
        where_clauses.append("v.id IN (SELECT video_id FROM video_tags WHERE tag_id = ?)")
        params.append(tag_id)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # 允许的排序字段
    allowed_sorts = {
        "added_at": "v.added_at",
        "published_at": "v.published_at",
        "title": "v.title",
        "view_count": "v.view_count",
        "duration": "v.duration",
        "priority": "v.priority",
    }
    sort_field = allowed_sorts.get(sort_by, "v.added_at")
    order = "ASC" if sort_order.upper() == "ASC" else "DESC"

    # 计算总数
    count_sql = f"SELECT COUNT(*) as total FROM videos v WHERE {where_sql}"
    total = conn.execute(count_sql, params).fetchone()["total"]

    # 分页查询
    offset = (page - 1) * per_page
    query_sql = f"""
        SELECT v.*, GROUP_CONCAT(t.name, ',') as tags
        FROM videos v
        LEFT JOIN video_tags vt ON v.id = vt.video_id
        LEFT JOIN tags t ON vt.tag_id = t.id
        WHERE {where_sql}
        GROUP BY v.id
        ORDER BY {sort_field} {order}
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    rows = conn.execute(query_sql, params).fetchall()

    videos = []
    for row in rows:
        video = dict(row)
        video["tags_list"] = video["tags"].split(",") if video["tags"] else []
        videos.append(video)

    conn.close()
    return {"videos": videos, "total": total, "page": page, "per_page": per_page}


def get_video(video_db_id):
    """获取单个视频详情"""
    conn = get_connection()
    row = conn.execute(
        """SELECT v.*, GROUP_CONCAT(t.name, ',') as tags
        FROM videos v
        LEFT JOIN video_tags vt ON v.id = vt.video_id
        LEFT JOIN tags t ON vt.tag_id = t.id
        WHERE v.id = ?
        GROUP BY v.id""",
        (video_db_id,),
    ).fetchone()
    conn.close()
    if row:
        video = dict(row)
        video["tags_list"] = video["tags"].split(",") if video["tags"] else []
        return video
    return None


def update_video_status(video_db_id, status):
    """更新视频翻译状态"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE videos SET translation_status = ?, updated_at = ? WHERE id = ?",
        (status, now, video_db_id),
    )
    conn.commit()
    conn.close()


def update_video_notes(video_db_id, notes):
    """更新视频备注"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE videos SET notes = ?, updated_at = ? WHERE id = ?",
        (notes, now, video_db_id),
    )
    conn.commit()
    conn.close()


def update_video_subtitle(video_db_id, subtitle_text):
    """保存视频字幕文本"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE videos SET subtitle_text = ?, subtitle_downloaded = 1, updated_at = ? WHERE id = ?",
        (subtitle_text, now, video_db_id),
    )
    conn.commit()
    conn.close()


def delete_video(video_db_id):
    """删除视频"""
    conn = get_connection()
    conn.execute("DELETE FROM videos WHERE id = ?", (video_db_id,))
    conn.commit()
    conn.close()


def get_dashboard_stats():
    """获取看板统计数据"""
    conn = get_connection()

    # 各状态数量
    status_counts = {}
    rows = conn.execute(
        "SELECT translation_status, COUNT(*) as cnt FROM videos GROUP BY translation_status"
    ).fetchall()
    for row in rows:
        status_counts[row["translation_status"]] = row["cnt"]

    # 总视频数
    total = conn.execute("SELECT COUNT(*) as cnt FROM videos").fetchone()["cnt"]

    # 各频道数量
    channel_counts = conn.execute(
        """SELECT channel_name, channel_id, COUNT(*) as cnt 
        FROM videos GROUP BY channel_id ORDER BY cnt DESC LIMIT 20"""
    ).fetchall()

    # 最近添加
    recent = conn.execute(
        "SELECT id, video_id, title, channel_name, thumbnail_url, added_at FROM videos ORDER BY added_at DESC LIMIT 10"
    ).fetchall()

    # 有字幕的视频数
    subtitle_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM videos WHERE subtitle_downloaded = 1"
    ).fetchone()["cnt"]

    conn.close()
    return {
        "status_counts": status_counts,
        "total": total,
        "channel_counts": [dict(r) for r in channel_counts],
        "recent": [dict(r) for r in recent],
        "subtitle_count": subtitle_count,
    }


# === 标签操作 ===

def add_tag(name, color="#007bff"):
    """添加标签"""
    conn = get_connection()
    try:
        conn.execute("INSERT INTO tags (name, color) VALUES (?, ?)", (name, color))
        conn.commit()
        tag_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return tag_id
    except sqlite3.IntegrityError:
        conn.close()
        return None


def get_all_tags():
    """获取所有标签"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_tag(tag_id):
    """删除标签"""
    conn = get_connection()
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()
    conn.close()


def add_video_tag(video_db_id, tag_id):
    """给视频添加标签"""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO video_tags (video_id, tag_id) VALUES (?, ?)",
            (video_db_id, tag_id),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def remove_video_tag(video_db_id, tag_id):
    """移除视频标签"""
    conn = get_connection()
    conn.execute(
        "DELETE FROM video_tags WHERE video_id = ? AND tag_id = ?",
        (video_db_id, tag_id),
    )
    conn.commit()
    conn.close()


# === 频道操作 ===

def add_channel(channel_data):
    """添加关注频道"""
    conn = get_connection()
    now = datetime.now().isoformat()
    try:
        conn.execute(
            """INSERT INTO channels (channel_id, name, description, thumbnail_url, 
            subscriber_count, added_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                channel_data["channel_id"],
                channel_data.get("name", ""),
                channel_data.get("description", ""),
                channel_data.get("thumbnail_url", ""),
                channel_data.get("subscriber_count", ""),
                now,
            ),
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_channels():
    """获取所有关注频道"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM channels ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_channel(channel_db_id):
    """删除关注频道"""
    conn = get_connection()
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_db_id,))
    conn.commit()
    conn.close()


def update_channel_last_checked(channel_id):
    """更新频道最后检查时间"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "UPDATE channels SET last_checked = ? WHERE channel_id = ?",
        (now, channel_id),
    )
    conn.commit()
    conn.close()


# === 搜索历史 ===

def add_search_history(keyword, results_count):
    """记录搜索历史"""
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO search_history (keyword, results_count, searched_at) VALUES (?, ?, ?)",
        (keyword, results_count, now),
    )
    conn.commit()
    conn.close()


def get_search_history(limit=20):
    """获取搜索历史"""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM search_history ORDER BY searched_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]