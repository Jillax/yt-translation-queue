"""
LLM 智能断句模块
使用 OpenAI 兼容 API 对字幕文本进行智能断句
"""

import re
import requests
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_CHARS_PER_LINE


def segment_transcript(transcript_text, max_chars=None, api_key=None, base_url=None, model=None):
    """
    使用 LLM 对字幕文本进行智能断句
    
    Args:
        transcript_text: 原始字幕文本（带时间戳）
        max_chars: 每条字幕最大英文字符数，默认使用配置值
        api_key: 自定义 API Key
        base_url: 自定义 API Base URL
        model: 自定义模型名
    
    Returns:
        dict: {"success": bool, "segmented": str, "error": str}
    """
    if not transcript_text or not transcript_text.strip():
        return {"success": False, "error": "字幕文本为空"}

    if max_chars is None:
        max_chars = LLM_MAX_CHARS_PER_LINE

    key = api_key or LLM_API_KEY
    url = base_url or LLM_BASE_URL
    mdl = model or LLM_MODEL

    if not key:
        return {"success": False, "error": "未配置 LLM API Key，请在 config.py 或环境变量中设置 LLM_API_KEY"}

    # 清理时间戳格式，保留 [mm:ss] 格式
    clean_text = _preprocess_transcript(transcript_text)

    # 截断过长的文本避免 API 超时
    if len(clean_text) > 6000:
        clean_text = clean_text[:6000]

    prompt = f"""You are a subtitle editor. Re-segment the following transcript for subtitle display.

RULES:
1. Each line AT MOST {max_chars} characters.
2. Break at natural sentence boundaries.
3. Preserve [mm:ss] timestamps. Use earliest timestamp if merging.
4. Output ONLY subtitle lines, no commentary.

TRANSCRIPT:
{clean_text}"""

    try:
        resp = requests.post(
            f"{url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": mdl,
                "messages": [
                    {"role": "system", "content": "You are a professional subtitle editor. Output only subtitle lines, no explanations."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            },
            timeout=180,
        )
        
        if resp.status_code != 200:
            return {"success": False, "error": f"LLM API 返回错误 {resp.status_code}: {resp.text[:300]}"}
        
        resp.raise_for_status()
        data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return {"success": False, "error": "LLM 返回内容为空"}

        # 清理结果
        lines = content.strip().split("\n")
        clean_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 去掉 markdown 代码块标记
            if line.startswith("```") or line.endswith("```"):
                continue
            clean_lines.append(line)

        segmented_text = "\n".join(clean_lines)

        return {
            "success": True,
            "segmented": segmented_text,
            "lines_count": len(clean_lines),
            "max_chars": max_chars,
            "model": mdl,
        }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "LLM 请求超时（120秒），请稍后重试"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"LLM API 请求失败: {str(e)}"}
    except (KeyError, IndexError) as e:
        return {"success": False, "error": f"LLM 响应解析失败: {str(e)}"}


def _preprocess_transcript(text):
    """
    预处理字幕文本，清理格式
    保留 [mm:ss] 时间戳前缀
    """
    lines = text.strip().split("\n")
    processed = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        processed.append(line)
    return "\n".join(processed)


def simple_segment(transcript_text, max_chars=None):
    """
    简单断句（不使用 LLM）
    按句号/逗号等标点符号在 max_chars 范围内断行
    """
    if max_chars is None:
        max_chars = LLM_MAX_CHARS_PER_LINE

    lines = transcript_text.strip().split("\n")
    result = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 提取时间戳
        ts_match = re.match(r"\[(\d{2}:\d{2})\]\s*(.*)", line)
        if ts_match:
            timestamp = ts_match.group(1)
            text = ts_match.group(2)
        else:
            timestamp = None
            text = line

        if len(text) <= max_chars:
            result.append(line)
            continue

        # 按标点断句
        parts = _split_text(text, max_chars)
        for part in parts:
            if timestamp:
                result.append(f"[{timestamp}] {part}")
            else:
                result.append(part)

    return "\n".join(result)


def _split_text(text, max_chars):
    """将长文本在标点符号处断开"""
    if len(text) <= max_chars:
        return [text]

    # 断句优先级：句号 > 分号 > 逗号 > 空格
    break_chars = [". ", "; ", ", ", " — ", " - ", " "]
    
    result = []
    remaining = text

    while len(remaining) > max_chars:
        # 在 max_chars 范围内找最佳断点
        best_break = -1
        for bc in break_chars:
            pos = remaining[:max_chars].rfind(bc)
            if pos > best_break and pos > max_chars * 0.3:  # 至少保留30%长度
                best_break = pos + len(bc)
        
        if best_break <= 0:
            # 找不到好的断点，强制在空格处断开
            best_break = remaining[:max_chars].rfind(" ")
            if best_break <= 0:
                best_break = max_chars
            else:
                best_break += 1

        result.append(remaining[:best_break].strip())
        remaining = remaining[best_break:].strip()

    if remaining:
        result.append(remaining)

    return result