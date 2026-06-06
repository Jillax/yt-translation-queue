import os

# === YouTube API 配置 ===
# 获取 API Key: https://console.cloud.google.com/apis/credentials
# 启用 YouTube Data API v3
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# === 搜索关键词配置 ===
DEFAULT_SEARCH_KEYWORDS = [
    "feminism explained",
    "gender studies lecture",
    "social justice education",
    "women's rights history",
    "intersectionality",
    "patriarchy explained",
    "gender equality",
    "feminist theory",
    "women empowerment education",
    "social construct gender",
    "reproductive rights",
    "women's suffrage history",
    "feminism philosophy",
    "gender inequality documentary",
    "women in history",
]

# === 关注频道配置 ===
# 格式: {"name": "显示名称", "channel_id": "UCxxxxxxxx"}
DEFAULT_CHANNELS = [
    # 示例频道（可自行添加）
    # {"name": "Philosophy Tube", "channel_id": "UCNIuvl7V8zACPpTmmNI7P2Q"},
    # {"name": "ContraPoints", "channel_id": "UCNvsIonJdJ5E4EXMa65VYpA"},
]

# === 翻译状态定义 ===
TRANSLATION_STATUSES = [
    ("pending", "待翻译", "#6c757d"),      # 灰色
    ("translating", "翻译中", "#ffc107"),   # 黄色
    ("translated", "已翻译", "#17a2b8"),    # 蓝色
    ("proofreading", "已校对", "#fd7e14"),  # 橙色
    ("published", "已发布", "#28a745"),     # 绿色
]

# === LLM 配置（OpenAI 兼容 API） ===
LLM_API_KEY = os.environ.get("LLM_API_KEY", "tp-c58etypj3tga8i9b3dyc53oioy3enww0izd1asxeeio5m7kx")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_MAX_CHARS_PER_LINE = int(os.environ.get("LLM_MAX_CHARS", "24"))

# === 应用配置 ===
APP_HOST = "127.0.0.1"
APP_PORT = 5000
DEBUG = True

# === 数据库路径 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "data", "videos.db")
SUBTITLES_DIR = os.path.join(BASE_DIR, "subtitles")

# 确保目录存在
os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
os.makedirs(SUBTITLES_DIR, exist_ok=True)