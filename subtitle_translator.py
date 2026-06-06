#!/usr/bin/env python3
"""
YouTube 视频字幕上下文翻译优化工具 v2

优化特性：
- 术语表提取：先通读全文提取关键术语对照表，翻译时强制约束
- 两遍翻译：第一遍翻译 + 第二遍审校润色
- 断点续传：每批保存进度，中断后可从断点继续
- 视频元信息注入：下载时提取标题/描述/频道，翻译时注入上下文

用法：
  python subtitle_translator.py download "https://youtube.com/watch?v=xxx"
  python subtitle_translator.py optimize "path/to/file.ass"
  python subtitle_translator.py optimize "path/to/file.ass" --no-review
  python subtitle_translator.py optimize "path/to/file.ass" --no-glossary
  python subtitle_translator.py test-api
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
VIDEOS_DIR = Path(os.environ.get("SUBTITLE_TRANSLATOR_VIDEOS_DIR", "./videos"))
SUBTITLE_DIR = Path(os.environ.get("SUBTITLE_TRANSLATOR_SUBTITLE_DIR", "./subtitles"))
PROGRESS_EXT = ".progress.json"
ass_path_global = ""

def load_config():
    if not CONFIG_PATH.exists():
        print("错误: config.json 不存在，请先配置 API 信息")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)

# ── 翻译记忆库（Translation Memory）────────────────────────────────────

TM_PATH = SCRIPT_DIR / "translation_memory.json"

def load_translation_memory() -> dict:
    """加载翻译记忆库: {英文原文: 中文翻译}"""
    if TM_PATH.exists():
        try:
            with open(TM_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def save_translation_memory(tm: dict):
    with open(TM_PATH, "w", encoding="utf-8") as f:
        json.dump(tm, f, ensure_ascii=False, indent=2)

def lookup_similar(text: str, tm: dict, threshold: float = 0.7) -> str | None:
    """从翻译记忆库中查找相似句子（简单单词重叠匹配）"""
    if not tm:
        return None
    text_words = set(text.lower().split())
    if len(text_words) < 3:
        return None
    best_match, best_score = None, 0
    for en, cn in tm.items():
        en_words = set(en.lower().split())
        if not en_words:
            continue
        overlap = len(text_words & en_words) / max(len(text_words), len(en_words))
        if overlap > best_score and overlap >= threshold:
            best_score = overlap
            best_match = cn
    return best_match

def update_translation_memory(english_texts: list, translations: list, tm: dict):
    """将成功的翻译存入记忆库"""
    for en, cn in zip(english_texts, translations):
        if cn and cn != "ERROR" and len(en.strip()) > 5:
            tm[en.strip()] = cn
    save_translation_memory(tm)


def load_human_feedback() -> list:
    """加载人工反馈（用户在UI上标记/修改的翻译）"""
    fb_path = SCRIPT_DIR / "human_feedback.json"
    if fb_path.exists():
        try:
            with open(fb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [(e["en"], e["cn"]) for e in data[-20:]]  # 最多20条
        except: pass
    return []


def get_client(config):
    from openai import OpenAI
    return OpenAI(api_key=config["api_key"], base_url=config["api_base"])

def llm_call(client, config, system_prompt, user_prompt, max_tokens=4096, temperature=0.3):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=config["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=120,
            )
            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                if content:
                    return content.strip()
                print(f"  ⚠ 第{attempt+1}次: 返回空内容")
            else:
                print(f"  ⚠ 第{attempt+1}次: 无效响应结构")
        except Exception as e:
            err_str = str(e)
            if "timeout" in err_str.lower():
                print(f"  ⚠ 第{attempt+1}次: 请求超时")
            else:
                print(f"  ❌ 第{attempt+1}次出错: {err_str[:100]}")
        if attempt < max_retries - 1:
            time.sleep(3 + attempt * 2)
    return None

def extract_json_array(text: str) -> list | None:
    try:
        result = json.loads(text)
        if isinstance(result, list): return result
    except json.JSONDecodeError: pass
    match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, list): return result
        except json.JSONDecodeError: pass
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end+1])
            if isinstance(result, list): return result
        except json.JSONDecodeError: pass
    return None

def extract_json_dict(text: str) -> dict | None:
    try:
        result = json.loads(text)
        if isinstance(result, dict): return result
    except json.JSONDecodeError: pass
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, dict): return result
        except json.JSONDecodeError: pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end+1])
            if isinstance(result, dict): return result
        except json.JSONDecodeError: pass
    return None



def score_translation(english: str, chinese: str) -> dict:
    """对单条翻译打分（0-10）"""
    if not chinese or chinese == "ERROR":
        return {"total": 0, "length_ok": False, "punct_ok": False}
    
    # 长度合理性（英文字符数的0.3-1.5倍为合理）
    len_ratio = len(chinese) / max(len(english), 1)
    length_score = 10 if 0.3 <= len_ratio <= 1.5 else (5 if 0.2 <= len_ratio <= 2.0 else 0)
    
    # 字数限制（<=25字满分，每超1字扣1分）
    len_penalty = max(0, len(chinese) - 25)
    length_score = max(0, length_score - len_penalty)
    
    # 标点规范（无句末句号/感叹号）
    punct_ok = not chinese.rstrip().endswith(('。', '！', '!', '.'))
    punct_score = 10 if punct_ok else 7
    
    # 空内容
    empty_score = 0 if len(chinese.strip()) < 2 else 10
    
    total = round((length_score * 0.3 + punct_score * 0.2 + empty_score * 0.5), 1)
    return {
        "total": total,
        "length_ok": 0.3 <= len_ratio <= 1.5,
        "punct_ok": punct_ok,
        "char_count": len(chinese),
    }


def validate_alignment(english_batch: list, translations: list) -> bool:
    """验证翻译与原文是否对齐（基本语义检查）"""
    if len(translations) != len(english_batch):
        return False
    if not english_batch or not translations:
        return True
    
    # 检查1: 首条翻译长度合理性（不应过短或过长）
    first_en = english_batch[0]
    first_cn = translations[0]
    if first_cn == "ERROR":
        return True  # ERROR占位跳过检查
    
    # 检查2: 翻译不应包含英文原文（说明可能错位了）
    # 如果中文翻译里出现了大量英文单词，可能错位
    en_word_count = sum(1 for w in first_en.split() if len(w) > 3 and w.lower() in first_cn.lower())
    if en_word_count > len(first_en.split()) * 0.5 and len(first_en.split()) > 5:
        return False  # 中文里有超过50%的英文单词，可能错位
    
    # 检查3: 最后一条翻译长度合理性
    last_cn = translations[-1]
    if last_cn != "ERROR" and len(last_cn.strip()) < 2 and len(english_batch[-1]) > 20:
        return False  # 最后一条原文很长但翻译极短，可能被截断
    
    return True
def clean_translation(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[。！!\.]+$', '', text)
    return text

def format_glossary(glossary: dict) -> str:
    if not glossary: return ""
    lines = []
    for en, cn in sorted(glossary.items()):
        lines.append(f"  {en} → {'（保留原文）' if en == cn else cn}")
    return "\n".join(lines)

# ── ASS 文件解析 ──────────────────────────────────────────────────────────────



def detect_video_style(english_texts: list) -> str:
    """检测字幕风格（学术/演讲/访谈/日常）"""
    sample = " ".join(english_texts[:30]).lower()
    academic_words = ["therefore", "hypothesis", "methodology", "research", "study", "analysis", "framework"]
    speech_words = ["ladies and gentlemen", "let me", "i want to", "today", "talk about", "share"]
    interview_words = ["question", "answer", "think", "opinion", "agree", "disagree"]
    
    scores = {
        "academic": sum(1 for w in academic_words if w in sample),
        "speech": sum(1 for w in speech_words if w in sample),
        "interview": sum(1 for w in interview_words if w in sample),
    }
    style = max(scores, key=scores.get)
    if scores[style] < 2:
        style = "general"
    return style

def get_style_hint(style: str) -> str:
    """根据风格返回翻译提示"""
    hints = {
        "academic": "这是一段学术内容，翻译应使用严谨的书面语，保持学术术语的准确性",
        "speech": "这是一段演讲/讲座内容，翻译应保持演讲的感染力和节奏感",
        "interview": "这是一段访谈内容，翻译应适度口语化但不过分随意",
        "general": "保持自然流畅的书面语风格",
    }
    return hints.get(style, hints["general"])

def compute_adaptive_batches(english_texts: list, base_size: int = 60) -> list:
    """根据句子平均长度自适应调整批次大小"""
    if not english_texts:
        return []
    avg_len = sum(len(t) for t in english_texts) / len(english_texts)
    if avg_len > 150:       # 长句子（学术/演讲）
        size = max(15, base_size // 4)
    elif avg_len > 80:      # 中等长度
        size = max(25, base_size // 2)
    else:                   # 短句子（日常/访谈）
        size = base_size
    print(f"  自适应批次: 平均{avg_len:.0f}字符/句 → 每批{size}条")
    return [(s, min(s + size, len(english_texts))) for s in range(0, len(english_texts), size)]

def parse_ass(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    header_end = 0
    for i, line in enumerate(lines):
        if line.strip() == "[Events]":
            header_end = i; break
    english_entries, chinese_entries = [], []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("Dialogue:"): continue
        if ",Secondary,," in stripped:
            english_entries.append((i, stripped.rsplit(",,", 1)[-1].strip()))
        elif ",Default,," in stripped:
            chinese_entries.append((i, stripped.rsplit(",,", 1)[-1].strip()))
    return lines[:header_end], english_entries, chinese_entries, lines

# ── 断点续传 ──────────────────────────────────────────────────────────────────

def get_progress_path(ass_path: str) -> Path:
    return Path(ass_path).with_suffix(PROGRESS_EXT)

def load_progress(ass_path: str) -> dict | None:
    path = get_progress_path(ass_path)
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return None

def save_progress(ass_path: str, progress: dict):
    path = get_progress_path(ass_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

def clear_progress(ass_path: str):
    path = get_progress_path(ass_path)
    if path.exists(): path.unlink()

# ── 子命令：下载视频 ─────────────────────────────────────────────────────────

def cmd_download(url: str):
    ensure_dir(VIDEOS_DIR)
    ffmpeg_available = False
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        ffmpeg_available = True
    except: pass
    if not ffmpeg_available:
        print("提示: ffmpeg 未安装，将仅下载单一格式\n")

    # 提取视频元信息
    print(f"正在获取视频信息: {url}\n")
    meta = {"url": url, "title": "", "channel": "", "description": ""}
    try:
        info_cmd = ["yt-dlp", "--no-playlist", "--encoding", "utf-8",
                     "--print", "%(title)s", "--print", "%(channel)s",
                     "--print", "%(description)s", "--no-download", url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, check=False)
        if info_result.returncode == 0:
            lines = info_result.stdout.strip().split("\n")
            if len(lines) >= 2:
                meta["title"] = lines[0]
                meta["channel"] = lines[1]
            if len(lines) >= 3:
                meta["description"] = "\n".join(lines[2:])
            print(f"标题: {meta['title']}")
            print(f"频道: {meta['channel']}")
            if meta["description"]:
                print(f"描述: {meta['description'][:200]}{'...' if len(meta['description']) > 200 else ''}")
            print()
    except Exception as e:
        print(f"获取元信息失败: {e}\n")

    # 下载视频
    print(f"正在下载视频... 保存目录: {VIDEOS_DIR}\n")
    cmd = ["yt-dlp", "--no-playlist", "-o", str(VIDEOS_DIR / "%(title)s.%(ext)s"), "--encoding", "utf-8"]
    if not ffmpeg_available:
        cmd += ["-f", "best[ext=mp4]/best"]
    else:
        cmd += ["-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode == 0:
            if meta["title"]:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', meta["title"])
                meta_path = VIDEOS_DIR / f"{safe_name}.meta.json"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                print(f"\n✅ 元信息已保存: {meta_path}")
            print("\n✅ 下载完成！")
            print("\n下一步：用 VideoCaptioner 处理视频，然后运行 optimize 命令")
        else:
            print(f"\n❌ 下载失败，退出码: {result.returncode}")
    except FileNotFoundError:
        print("错误: yt-dlp 未安装。请运行: pip install yt-dlp")
        sys.exit(1)

def find_video_meta(ass_path: str) -> dict | None:
    ass_stem = Path(ass_path).stem
    clean_stem = re.sub(r'-(双语|上下文优化|原始字幕|中文|英语|英文).*$', '', ass_stem)
    if not VIDEOS_DIR.exists(): return None
    for meta_file in VIDEOS_DIR.glob("*.meta.json"):
        meta_title = meta_file.stem.replace(".meta", "")
        if clean_stem in meta_title or meta_title in clean_stem:
            with open(meta_file, "r", encoding="utf-8") as f: return json.load(f)
    return None

# ── 术语表提取 ────────────────────────────────────────────────────────────────

GLOSSARY_PROMPT = """你是一位专业的翻译术语专家。从英文视频字幕中提取关键术语并给出中文翻译。

规则：
1. 提取专有名词（人名、地名、组织、概念、理论名称等）
2. 提取反复出现的重要词汇或短语
3. 人名保留英文原文不翻译
4. 返回JSON对象，key是英文术语，value是中文翻译

返回格式：{"term1": "翻译1", "term2": "翻译2", ...}"""

def extract_glossary(client, config, english_texts: list) -> dict:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    max_sample = 30
    sample = english_texts[:max_sample] if len(english_texts) > max_sample else english_texts
    if len(english_texts) > max_sample:
        print(f"  字幕较多（{len(english_texts)}条），使用前{max_sample}条提取术语")
    numbered = [f"{i+1}. {t}" for i, t in enumerate(sample)]
    user_prompt = "从以下字幕中提取专有名词和术语，返回JSON:\n\n" + "\n".join(numbered)
    print("  正在提取术语表...")

    # 用线程池+超时防止 API 调用卡死
    def _do_extract():
        return llm_call(client, config, GLOSSARY_PROMPT, user_prompt, max_tokens=1024)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_extract)
            content = future.result(timeout=90)  # 最多等90秒
    except (FutureTimeout, Exception) as e:
        print(f"  ⚠ 术语表提取超时或失败，跳过术语表继续翻译")
        return {}

    if content is None:
        print("  ⚠ 术语表提取失败，将跳过术语表继续翻译"); return {}
    glossary = extract_json_dict(content)
    if glossary is None:
        print("  ⚠ 术语表解析失败，将跳过术语表继续翻译"); return {}
    print(f"  ✅ 提取到 {len(glossary)} 个术语")
    return glossary

# ── 翻译 System Prompt ───────────────────────────────────────────────────────

def build_translate_system_prompt(glossary: dict, video_meta: dict | None, few_shot: str = "") -> str:
    parts = [
        "你是一位资深字幕翻译师，拥有10年YouTube视频翻译经验，精通中英文化差异。",
        "你的翻译追求信达雅——忠实原文、表达流畅、文字优雅，尤其注重中文的自然流畅。",
        "", "核心规则：",
        "1. 【数量硬约束】你必须返回与原文完全相同数量的翻译，一条不多一条不少。这是最重要的规则",
        "2. 【思维链】先通读全部字幕，理解视频主题、论述逻辑和上下文。对每条字幕，在内心分析其语境含义和翻译难点，然后再输出翻译",
        "3. 每条字幕独立翻译，逐条对应原文编号",
        "4. 【语序调整】通读全文理解上下文后，允许在语义相关的连续字幕范围内（不超过6条）微调语序，使中文表达更自然。调过后必须回归1:1对应。调序的目的是让中文更流畅，必须保证：(a) 返回数量与原文严格1:1对应 (b) 每条翻译仍忠实于对应编号原文的含义",
        "5. 【禁止操作】禁止合并两条原文为一条翻译，禁止跳过任何原文",
        "6. 【长度控制】每条译文不超过25个汉字。视频字幕有时间轴，过长观众来不及阅读，超过时精简",
        "7. 翻译风格：书面语，不过分口语化，也不要文绉绉",
        "8. 不要补充括号注释或额外解释",
        "9. 句末不要加句号、感叹号等结尾标点，但问号和句中的逗号、顿号等要保留以确保可读性",
        "10. 保留阿拉伯数字，不要转换为汉字数字（如保留\"3\"而不是\"三\"）",
        "11. 人名如果是英文人名，保留英文原文不翻译",
        "12. 同时提取本批中出现的专有名词和关键术语",
    ]

    # Few-shot 范例
    if few_shot:
        parts += ["", few_shot]

    if video_meta:
        parts += ["", "【视频信息】"]
        if video_meta.get("title"): parts.append(f"标题: {video_meta['title']}")
        if video_meta.get("channel"): parts.append(f"频道: {video_meta['channel']}")
        if video_meta.get("description"): parts.append(f"描述: {video_meta['description'][:500]}")
    if glossary:
        parts += ["", "【已有术语表——必须严格遵守】", format_glossary(glossary)]
    parts += ["", "返回JSON对象，包含translations和terms两个字段：",
              '{"translations":["翻译1","翻译2",...],"terms":{"English Term":"中文翻译",...}}']
    return "\n".join(parts)

REVIEW_PROMPT = """你是一位专业的翻译审校专家。你将收到英文字幕原文和中文翻译初稿。你的任务不仅是逐条校对，更要从段落整体视角确保语义连贯。

【最关键任务】逐条检查中英文是否对齐：
- 第N条中文必须对应第N条英文的含义
- 如果发现某条翻译对应了错误的英文原文，必须修正
- 如果发现系统性偏移（从某条开始全部错位），必须报告并修正
- 如果发现合并（两条英文合成一条中文）或跳过，必须拆分/补回

审校要求：
1. 翻译准确忠实于对应编号的英文原文
2. 中文语序自然流畅
3. 全文术语翻译一致
4. 每条译文不超过25个汉字
5. 翻译风格：书面语
6. 不要补充括号注释
7. 句末不要加句号、感叹号，但问号和句中的逗号、顿号等要保留以确保可读性
8. 保留阿拉伯数字
9. 人名保留英文原文
10. 检查是否有不合理的语序调整导致上下文断裂

返回数量必须与原文完全一致。
对每条翻译：如果已经很好就原样返回；如果需要修改，必须同时在内部思考中确认修改理由（如"语序调整""术语统一""更准确"等），然后返回修改后的翻译。
返回格式：["修改后的翻译1", "修改后的翻译2", ...]"""

def build_user_prompt(english_lines: list, batch_start: int, full_texts: list = None, translated_context: list = None) -> str:
    """构建翻译用户 prompt，含前后3条重叠上下文"""
    parts = []

    # 前文参考：从完整列表中取 batch_start 前3条
    if full_texts and batch_start > 0:
        ctx_start = max(0, batch_start - 3)
        before = full_texts[ctx_start:batch_start]
        if before:
            parts.append("【前文参考（不需要翻译，仅供理解上下文连贯性）】")
            for i, t in enumerate(before):
                parts.append(f"[参考] 英文: {t}")
                if translated_context:
                    ctx_idx = ctx_start + i
                    if ctx_idx < len(translated_context) and translated_context[ctx_idx] != "ERROR":
                        parts.append(f"[参考] 中文: {translated_context[ctx_idx]}")
            parts.append("")

    # 正文
    numbered = [f"[{batch_start + i + 1}] {t}" for i, t in enumerate(english_lines)]
    parts.append(f"以下是需要翻译的字幕（共{len(english_lines)}条），请逐条翻译并提取术语：\n")
    parts.extend(numbered)

    # 后文参考
    if full_texts:
        batch_end = batch_start + len(english_lines)
        if batch_end < len(full_texts):
            after = full_texts[batch_end:batch_end+3]
            if after:
                parts.append("\n【后文参考（不需要翻译，仅供理解上下文连贯性）】")
                for t in after:
                    parts.append(f"[参考] {t}")

    parts.append('\n返回JSON: {"translations":["翻译1",...],"terms":{"英文":"中文",...}}')
    return "\n".join(parts)

def build_review_prompt(english_texts: list, draft_texts: list, batch_start: int) -> str:
    pairs = [f"[{batch_start + i + 1}]\n原文: {en}\n翻译: {cn}" for i, (en, cn) in enumerate(zip(english_texts, draft_texts))]
    return f"以下是对{len(pairs)}条字幕的原文和翻译初稿，请逐条审校后返回改进翻译：\n\n" + "\n\n".join(pairs)

# ── 核心翻译流程（带断点续传+动态术语）─────────────────────────────────────────

def run_translation_pass(client, config, english_texts: list, system_prompt: str,
                          prompt_builder, max_per_batch: int, progress: dict,
                          pass_name="翻译", review_texts=None, glossary=None,
                          system_prompt_builder=None) -> list:
    global ass_path_global
    total = len(english_texts)
    all_translations = list(progress.get("translations", []))
    done_count = len(all_translations)
    if done_count > 0:
        print(f"\n  发现已完成 {done_count}/{total} 条（断点续传）")

    batches = compute_adaptive_batches(english_texts, max_per_batch) if pass_name == "翻译" else [(s, min(s + max_per_batch, total)) for s in range(0, total, max_per_batch)]
    dynamic_terms = {}

    for batch_idx, (start, end) in enumerate(batches):
        if start < done_count: continue
        batch = english_texts[start:end]
        batch_num = batch_idx + 1
        print(f"\n  ── {pass_name} 第{batch_num}/{len(batches)}批 [{start+1}-{end}] ──")
        print(f"  发送 {len(batch)} 条到LLM...")

        # 如果提供了 prompt 构建回调，每批重建 system_prompt 以注入最新术语
        current_system_prompt = system_prompt_builder(glossary) if (system_prompt_builder and glossary is not None) else system_prompt

        # 翻译时传入完整文本列表用于上下文重叠；审校时用原始签名
        if review_texts:
            user_prompt = prompt_builder(batch, review_texts[start:end], start)
        else:
            user_prompt = prompt_builder(batch, start, full_texts=english_texts, translated_context=all_translations)

        batch_done = False
        for attempt in range(3):
            # 温度梯度：术语提取0.1, 翻译0.3, 审校0.5
            _temp = {"术语": 0.1, "翻译": 0.3, "审校": 0.5}.get(pass_name, 0.3)
            response_text = llm_call(client, config, current_system_prompt, user_prompt, temperature=_temp)
            if response_text is None:
                if attempt < 2: print(f"  重试中..."); time.sleep(2)
                continue

            # 尝试解析新格式：{translations: [...], terms: {...}}
            parsed = try_parse_response(response_text)
            if parsed is None:
                translations = extract_json_array(response_text)
                if translations is None:
                    print(f"  ⚠ 第{attempt+1}次: 无法解析JSON")
                    if attempt < 2: time.sleep(2)
                    continue
                terms = {}
            else:
                translations = parsed.get("translations", [])
                terms = parsed.get("terms", {})
                if not translations:
                    translations = extract_json_array(response_text)
                    if translations is None:
                        print(f"  ⚠ 第{attempt+1}次: 无法解析JSON")
                        if attempt < 2: time.sleep(2)
                        continue

            if len(translations) != len(batch):
                print(f"  ⚠ 第{attempt+1}次: 返回{len(translations)}条，期望{len(batch)}条")
                if translations:
                    while len(translations) < len(batch): translations.append("ERROR")
                    translations = translations[:len(batch)]
                if attempt < 2: time.sleep(2); continue
            
            # 对齐验证：检查翻译是否与原文语义对齐
            if not validate_alignment(batch, translations):
                print(f"  ⚠ 第{attempt+1}次: 对齐验证失败（翻译与原文不匹配）")
                if attempt < 2: time.sleep(2); continue

            # 合并动态术语（去重：只注入新术语）
            if terms and glossary is not None:
                new_count = 0
                for en, cn in terms.items():
                    en = en.strip()
                    cn = str(cn).strip()
                    if not en or not cn:
                        continue
                    en_lower = en.lower()
                    # 去重：检查是否已存在（大小写不敏感）
                    already_exists = en_lower in {k.lower() for k in glossary}
                    if not already_exists:
                        glossary[en] = cn
                        dynamic_terms[en] = cn
                        new_count += 1
                if new_count > 0:
                    print(f"  📖 新增 {new_count} 个术语（累计 {len(glossary)} 个）")
                    progress["glossary"] = dict(glossary)

            print(f"  ✅ 成功获取{len(translations)}条{pass_name}")
            all_translations.extend(translations)
            progress["translations"] = all_translations
            save_progress(ass_path_global, progress)
            batch_done = True
            break

        if not batch_done:
            # 批级重试：等待更长时间后重试一次
            print(f"  批次{batch_num}失败，等待10秒后重试整批...")
            time.sleep(10)
            # 第二次尝试
            for retry_att in range(2):
                response_text = llm_call(client, config, current_system_prompt, user_prompt)
                if response_text is None:
                    time.sleep(5); continue
                parsed = try_parse_response(response_text)
                if parsed is None:
                    translations = extract_json_array(response_text)
                    terms = {}
                else:
                    translations = parsed.get("translations", [])
                    terms = parsed.get("terms", {})
                if translations and len(translations) == len(batch):
                    print(f"  ✅ 重试成功！获取{len(translations)}条{pass_name}")
                    all_translations.extend(translations)
                    progress["translations"] = all_translations
                    save_progress(ass_path_global, progress)
                    if terms and glossary is not None:
                        for en, cn in terms.items():
                            en, cn = en.strip(), str(cn).strip()
                            if en and cn and en.lower() not in {k.lower() for k in glossary}:
                                glossary[en] = cn
                                dynamic_terms[en] = cn
                    batch_done = True
                    break
                time.sleep(5)
            if not batch_done:
                print(f"  批次{batch_num}重试后仍失败，使用ERROR占位")
                all_translations.extend(["ERROR"] * len(batch))
                progress["translations"] = all_translations
                save_progress(ass_path_global, progress)
        time.sleep(1)

    if dynamic_terms:
        print(f"\n  📖 动态术语共发现 {len(dynamic_terms)} 个新术语")
    return all_translations


def try_parse_response(text: str) -> dict | None:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r'^```(?:json)?\s*', '', clean)
        clean = re.sub(r'\s*```$', '', clean)
    try:
        result = json.loads(clean)
        if isinstance(result, dict) and "translations" in result:
            return result
    except json.JSONDecodeError:
        pass
    start = clean.find('{')
    end = clean.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(clean[start:end+1])
            if isinstance(result, dict) and "translations" in result:
                return result
        except json.JSONDecodeError:
            pass
    return None


def load_few_shot_examples(subtitle_dir: str = str(SUBTITLE_DIR), max_examples: int = 5) -> list[tuple[str, str]]:
    """从已有的优秀翻译文件中提取英中文对照作为 few-shot 范例"""
    examples = []
    sub_dir = Path(subtitle_dir)
    if not sub_dir.exists():
        return examples

    # 找"上下文优化"文件（这些是人工审校过的高质量翻译）
    optimized_files = sorted(sub_dir.glob("*上下文优化*.ass"), key=lambda f: f.stat().st_mtime)

    for f in optimized_files:
        if len(examples) >= max_examples:
            break
        try:
            with open(f, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            # 提取英中文对
            en_lines = []
            cn_lines = []
            for line in lines:
                s = line.strip()
                if not s.startswith("Dialogue:"):
                    continue
                if ",Secondary,," in s:
                    en_lines.append(s.rsplit(",,", 1)[-1].strip())
                elif ",Default,," in s:
                    cn_lines.append(s.rsplit(",,", 1)[-1].strip())
            # 取前3对作为范例
            if len(en_lines) >= 3 and len(cn_lines) >= 3:
                for i in range(min(3, len(en_lines), len(cn_lines))):
                    if en_lines[i] and cn_lines[i] and cn_lines[i] != "ERROR":
                        examples.append((en_lines[i], cn_lines[i]))
                        if len(examples) >= max_examples:
                            return examples
        except Exception:
            continue
    return examples


def build_few_shot_text(examples: list[tuple[str, str]]) -> str:
    """构建 few-shot 范例文本"""
    if not examples:
        return ""
    lines = ["【翻译范例——请参照以下翻译风格】"]
    for i, (en, cn) in enumerate(examples, 1):
        lines.append(f"  英文: {en}")
        lines.append(f"  中文: {cn}")
        if i < len(examples):
            lines.append("")
    return "\n".join(lines)


def run_back_translation_check(client, config, english_texts: list,
                                 translations: list, sample_size: int = 20) -> list[int]:
    """回译验证：随机抽样翻译回英文，与原文对比，返回可疑条目的索引"""
    import random
    total = min(len(english_texts), len(translations))
    if total < 10:
        return []

    # 均匀采样
    indices = sorted(random.sample(range(total), min(sample_size, total)))
    pairs = []
    for idx in indices:
        if translations[idx] and translations[idx] != "ERROR":
            pairs.append((idx, english_texts[idx], translations[idx]))

    if len(pairs) < 5:
        return []

    # 构建回译 prompt
    numbered = []
    for i, (idx, en, cn) in enumerate(pairs):
        numbered.append(f"[{i+1}] 中文: {cn}")

    user_prompt = (
        "请将以下中文翻译回英文，逐条返回 JSON 数组：\n\n"
        + "\n".join(numbered)
    )

    print("  正在执行回译验证...")
    content = llm_call(client, config, "你是翻译专家。将中文翻译为英文，返回JSON数组。", user_prompt, max_tokens=2048)
    if content is None:
        return []

    back_translations = extract_json_array(content)
    if not back_translations or len(back_translations) != len(pairs):
        return []

    # 对比：用简单字符重叠度衡量相似度
    suspicious = []
    for i, (idx, original_en, cn) in enumerate(pairs):
        back_en = back_translations[i].strip().lower()
        orig_lower = original_en.strip().lower()
        # 简单相似度：共同单词数 / 总单词数
        orig_words = set(orig_lower.split())
        back_words = set(back_en.split())
        if not orig_words:
            continue
        overlap = len(orig_words & back_words) / max(len(orig_words), 1)
        if overlap < 0.3:  # 相似度低于30%认为可疑
            suspicious.append(idx)

    return suspicious


def generate_short_title(client, config, english_texts: list) -> str | None:
    sample = "\n".join(english_texts[:20])
    try:
        response = client.chat.completions.create(
            model=config["model"],
            messages=[
                {"role": "system", "content": "你是翻译专家。根据以下英文字幕内容，生成一个简短的中文标题（不超过10个字，不要标点，不要引号）。只返回标题文字。"},
                {"role": "user", "content": sample},
            ],
            temperature=0.3, max_tokens=50, timeout=30,
        )
        if response.choices and response.choices[0].message.content:
            title = response.choices[0].message.content.strip()
            title = re.sub(r'[「」『』""''《》【】\[\]()（）.,，。!！?？:：;；]', '', title)
            return title[:15] if title else None
    except:
        pass
    return None

# ── 子命令：优化字幕 ─────────────────────────────────────────────────────────

def cmd_optimize(ass_path: str, no_review=False, no_glossary=False):
    global ass_path_global
    ass_path_global = ass_path
    if not os.path.exists(ass_path):
        print(f"错误: 文件不存在: {ass_path}"); sys.exit(1)

    config = load_config()
    client = get_client(config)
    print(f"正在解析: {ass_path}")
    header, english_entries, chinese_entries, all_lines = parse_ass(ass_path)
    eng_count, chn_count = len(english_entries), len(chinese_entries)
    print(f"英文行数: {eng_count}  中文行数: {chn_count}")
    if eng_count == 0:
        print("错误: 未找到英文字幕行"); sys.exit(1)

    english_texts = [text for _, text in english_entries]
    max_per_batch = config.get("max_lines_per_batch", 80)
    progress = load_progress(ass_path) or {}

    # 阶段1：术语表
    # 优先加载手动术语库（glossary.json）
    manual_glossary = {}
    glossary_file = SCRIPT_DIR / "glossary.json"
    if glossary_file.exists():
        try:
            with open(glossary_file, "r", encoding="utf-8") as gf:
                manual_glossary = json.load(gf)
            if manual_glossary:
                print(f"\n加载手动术语库: {len(manual_glossary)} 个术语")
        except: pass

    glossary = progress.get("glossary", {})
    glossary.update(manual_glossary)  # 手动术语优先覆盖动态术语
    if no_glossary:
        print("\n跳过术语表提取（--no-glossary）"); glossary = {}
    elif not glossary:
        print("\n━━ 阶段 1/3: 术语表提取 ━━")
        glossary = extract_glossary(client, config, english_texts)
        progress["glossary"] = glossary; save_progress(ass_path, progress)
    else:
        print(f"\n━━ 阶段 1/3: 术语表已缓存（{len(glossary)}个术语） ━━")

    # 加载翻译记忆库
    tm = load_translation_memory()
    if tm:
        print(f"\n加载翻译记忆库: {len(tm)} 条历史翻译")

    video_meta = find_video_meta(ass_path)
    if video_meta: print(f"找到视频元信息: {video_meta.get('title', '未知')}")

    # 加载 Few-shot 范例
    few_shot_examples = load_few_shot_examples()
    few_shot_text = build_few_shot_text(few_shot_examples)
    if few_shot_examples:
        print(f"加载了 {len(few_shot_examples)} 条翻译范例")

    # 阶段2：第一遍翻译（带动态术语提取）
    draft_translations = progress.get("draft_translations", [])
    if len(draft_translations) < eng_count:
        if draft_translations:
            print(f"\n━━ 阶段 2/3: 继续第一遍翻译（已完成 {len(draft_translations)}/{eng_count} 条）━━")
        else:
            print("\n━━ 阶段 2/3: 第一遍翻译（Draft）━━")
        system_prompt = build_translate_system_prompt(glossary, video_meta, few_shot_text)

        # 用 lambda 每批重建 system_prompt，使动态术语能注入后续批次
        def _build_prompt(g):
            return build_translate_system_prompt(g, video_meta, few_shot_text)

        # 将已有的 draft_translations 同步到 progress["translations"] 供 run_translation_pass 断点续传
        progress["translations"] = list(draft_translations)

        draft_translations = run_translation_pass(client, config, english_texts, system_prompt,
                                                   build_user_prompt, max_per_batch, progress,
                                                   "翻译", glossary=glossary,
                                                   system_prompt_builder=_build_prompt)
        progress["draft_translations"] = draft_translations
        save_progress(ass_path, progress)
    else:
        print(f"\n━━ 阶段 2/3: 第一遍翻译已缓存（{len(draft_translations)}条） ━━")

    draft_translations = [clean_translation(t) for t in draft_translations]
    draft_errors = sum(1 for t in draft_translations if t == "ERROR")
    print(f"\n第一遍完成: {eng_count - draft_errors}/{eng_count} 条成功")

    # 阶段3：第二遍审校
    if no_review:
        print("\n跳过第二遍审校（--no-review）")
        final_translations = draft_translations
    else:
        final_translations = progress.get("final_translations", [])
        if len(final_translations) < eng_count:
            if final_translations:
                print(f"\n━━ 阶段 3/3: 继续第二遍审校（已完成 {len(final_translations)}/{eng_count} 条）━━")
            else:
                print("\n━━ 阶段 3/3: 第二遍审校（Review）━━")
            review_progress = {"translations": list(final_translations)}
            review_translations = run_translation_pass(client, config, english_texts, REVIEW_PROMPT,
                                                        build_review_prompt, max_per_batch, review_progress,
                                                        "审校", review_texts=draft_translations)
            final_translations = review_translations
            progress["final_translations"] = final_translations
            save_progress(ass_path, progress)
        else:
            print(f"\n━━ 阶段 3/3: 审校结果已缓存 ━━")
        final_translations = [clean_translation(t) for t in final_translations]

    # 回译验证（抽样检查翻译准确性）
    if not no_review and eng_count >= 10:
        print("\n━━ 回译验证 ━━")
        suspicious = run_back_translation_check(client, config, english_texts, final_translations)
        if suspicious:
            print(f"  ⚠ 发现 {len(suspicious)} 条可疑翻译（编号: {[i+1 for i in suspicious[:10]]}...）")
            print(f"  这些翻译的回译与原文相似度较低，建议人工审核")
        else:
            print(f"  ✅ 抽样验证通过，翻译与原文语义一致")

    # 替换ASS
    if len(final_translations) < eng_count:
        final_translations.extend(["ERROR"] * (eng_count - len(final_translations)))
    final_translations = final_translations[:eng_count]
    error_count = sum(1 for t in final_translations if t == "ERROR")
    print(f"\n最终翻译完成: {eng_count - error_count}/{eng_count} 条成功")

    # 翻译统计
    valid_translations = [t for t in final_translations if t != "ERROR"]
    if valid_translations:
        avg_len = sum(len(t) for t in valid_translations) / len(valid_translations)
        max_len = max(len(t) for t in valid_translations)
        over_25 = sum(1 for t in valid_translations if len(t) > 25)
        print(f"\n━━ 翻译统计 ━━")
        print(f"  成功率: {len(valid_translations)}/{eng_count} ({len(valid_translations)*100//eng_count}%)")
        print(f"  平均字数: {avg_len:.1f}  最大字数: {max_len}")
        if over_25:
            print(f"  ⚠ 超过25字的条目: {over_25}条")
        if glossary:
            # 检查术语覆盖率
            used_terms = sum(1 for t in valid_translations for term in glossary.values() if term in t)
            print(f"  术语表: {len(glossary)}个术语")

    # 全局对齐检查
    if len(final_translations) == eng_count:
        misaligned = 0
        for i, (en, cn) in enumerate(zip(english_texts, final_translations)):
            if cn != "ERROR" and len(cn.strip()) < 2 and len(en) > 30:
                misaligned += 1
        if misaligned > 0:
            print(f"  ⚠ 对齐检查: 发现 {misaligned} 条可能错位的翻译")

    new_lines = all_lines.copy()
    chn_idx = replaced = 0
    for i, line in enumerate(new_lines):
        if not line.strip().startswith("Dialogue:"): continue
        if ",Default,," not in line: continue
        if chn_idx < len(final_translations):
            parts = new_lines[i].rsplit(",,", 1)
            if len(parts) == 2:
                new_lines[i] = parts[0] + ",," + final_translations[chn_idx] + "\n"
                replaced += 1
            chn_idx += 1
    print(f"替换中文行: {replaced}/{chn_count}")

    # 生成输出文件名
    base, ext = os.path.splitext(ass_path)
    output_path = f"{base}-上下文优化{ext}"
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    clear_progress(ass_path)
    print(f"\n✅ 优化完成！输出文件: {output_path}")

# ── 测试API ──────────────────────────────────────────────────────────────────

def cmd_test_api():
    config = load_config()
    client = get_client(config)
    print(f"API端点: {config['api_base']}\n模型: {config['model']}")
    print("\n正在查询可用模型...")
    try:
        models = client.models.list()
        print(f"可用模型: {[m.id for m in models.data[:10]]}")
    except Exception as e:
        print(f"查询失败: {e}")
    print("\n正在测试翻译...")
    try:
        response = client.chat.completions.create(model=config["model"],
            messages=[{"role": "system", "content": "你是翻译助手"},
                      {"role": "user", "content": '翻译为中文，返回JSON数组: ["Hello world", "This is a test"]'}],
            temperature=0.3, max_tokens=256)
        print(f"响应: {response.choices[0].message.content}")
        print("\n✅ API测试通过！")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")

# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouTube视频字幕上下文翻译优化工具v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例:\n  python subtitle_translator.py download "https://youtube.com/watch?v=xxx"\n'
               '  python subtitle_translator.py optimize "subtitle.ass"\n'
               '  python subtitle_translator.py optimize "subtitle.ass" --no-review\n'
               '  python subtitle_translator.py test-api')
    subparsers = parser.add_subparsers(dest="command")
    dl = subparsers.add_parser("download", help="下载YouTube视频")
    dl.add_argument("url", help="YouTube视频链接")
    opt = subparsers.add_parser("optimize", help="优化ASS字幕翻译")
    opt.add_argument("ass_file", help="ASS字幕文件路径")
    opt.add_argument("--no-review", action="store_true", help="跳过审校")
    opt.add_argument("--no-glossary", action="store_true", help="跳过术语表")
    subparsers.add_parser("test-api", help="测试API")
    args = parser.parse_args()
    if args.command == "download": cmd_download(args.url)
    elif args.command == "optimize": cmd_optimize(args.ass_file, args.no_review, args.no_glossary)
    elif args.command == "test-api": cmd_test_api()
    else: parser.print_help()

if __name__ == "__main__":
    main()

