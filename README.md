# YT Translation Queue — YouTube 翻译待译库

一个用于搜索、收集和管理 YouTube 视频翻译任务的 Web 应用。专注于人文社科 × 教育 × 女性主义相关内容。

## ✨ 功能特点

### 🔍 搜索与发现
- 按关键词搜索 YouTube 视频（支持 YouTube Data API v3 或 yt-dlp 免 API Key 搜索）
- 内置推荐关键词（feminism, gender studies, social justice 等）
- 搜索历史记录

### 📋 待译库管理
- 视频卡片网格展示（缩略图、标题、频道、时长、观看次数）
- **翻译状态流转**：待翻译 → 翻译中 → 已翻译 → 已校对 → 已发布
- **标签系统**：自定义标签分类（女性主义、教育、社会学等）
- **筛选与排序**：按状态、标签、频道、关键词筛选，多字段排序
- 分页浏览

### 📝 字幕抓取
- 自动获取 YouTube 字幕（支持自动字幕和手动字幕）
- 带时间戳的字幕文本展示
- 导出 SRT 格式字幕文件
- 字幕自动保存到数据库

### 📡 频道管理
- 添加关注的 YouTube 频道
- 一键获取频道最新视频并入库
- 频道订阅信息展示

### 📊 数据看板
- 翻译状态分布统计（进度条可视化）
- 频道分布 Top 10
- 最近添加视频列表
- 字幕获取率统计

## 🚀 快速开始

### 方式一：双击启动（Windows）
```
双击 start.bat
```

### 方式二：命令行启动
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动应用
python app.py
```

启动后访问 **http://127.0.0.1:5000**

## ⚙️ 配置

### YouTube API Key（可选）

有两种搜索模式：
- **有 API Key**：使用 YouTube Data API v3，速度快、结果准确
- **无 API Key**：使用 yt-dlp 搜索，速度较慢但无需配置

获取 API Key：
1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目并启用 **YouTube Data API v3**
3. 创建 API Key
4. 设置方式（二选一）：
   - 环境变量：`set YOUTUBE_API_KEY=你的Key`
   - 编辑 `config.py` 中的 `YOUTUBE_API_KEY`

### 搜索关键词

编辑 `config.py` 中的 `DEFAULT_SEARCH_KEYWORDS` 列表，自定义推荐关键词。

## 📁 项目结构

```
yt-translation-queue/
├── app.py                  # Flask 主应用（路由 + API）
├── config.py               # 配置文件（API Key、关键词等）
├── database.py             # SQLite 数据库操作
├── youtube_scraper.py      # YouTube 搜索与字幕抓取
├── requirements.txt        # Python 依赖
├── start.bat               # Windows 一键启动脚本
├── data/                   # SQLite 数据库文件（自动创建）
├── subtitles/              # 字幕文件存放（自动创建）
└── templates/              # Web 页面模板
    ├── base.html           # 基础布局（侧边栏 + 样式）
    ├── index.html          # 首页/视频列表
    ├── search.html         # 搜索页
    ├── video_detail.html   # 视频详情/字幕查看
    ├── channels.html       # 频道管理
    └── dashboard.html      # 数据看板
```

## 🔧 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python + Flask |
| 数据库 | SQLite |
| 前端 | Bootstrap 5 + Bootstrap Icons |
| YouTube 搜索 | YouTube Data API v3 / yt-dlp |
| 字幕抓取 | youtube-transcript-api / yt-dlp |

## 📖 使用流程

1. **搜索** → 在搜索页输入关键词或使用推荐关键词
2. **入库** → 选择感兴趣的视频，点击"添加到待译库"
3. **获取字幕** → 在视频详情页点击"获取字幕"
4. **管理状态** → 随翻译进度更新状态标签
5. **添加标签** → 为视频打上主题标签方便分类
6. **导出** → 下载 SRT 字幕文件进行翻译

## 💡 推荐关注的频道主题

- 女性主义理论与历史
- 性别研究学术讲座
- 社会正义教育
- 人文社科纪录片
- 哲学与社会学