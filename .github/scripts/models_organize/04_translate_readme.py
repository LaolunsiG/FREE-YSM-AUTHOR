import os
import re
import sys
from pathlib import Path

# openai 依赖缺失时优雅跳过（本地无此库/API key 时不应 traceback）
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths  # noqa: E402
from lib import readme as lib_readme  # noqa: E402

ROOT = lib_paths.WORKSPACE_ROOT
ZH_README = ROOT / 'README.md'
EN_README = ROOT / 'Docs' / 'README-EN.md'

# AUTHORS_LIST 自动化区域标记（与 03_generate_root_readme.py --author 共用 lib/readme 常量）
AUTHORS_START = lib_readme.AUTHORS_LIST_START
AUTHORS_END = lib_readme.AUTHORS_LIST_END


def call_llm_translate(text, client, model):
    """仅翻译有变动的中文段落"""
    system_prompt = (
        "You are a professional software documentation translator. "
        "Translate the input Chinese text into natural and clear English for a GitHub Markdown document.\n"
        "Rules:\n"
        "1. Keep all Markdown structure, HTML details elements, and URLs intact.\n"
        "2. Do NOT translate file paths (Models/0000), file extensions (.ysm), author IDs (#0001), or repository links.\n"
        "3. Translate commit/changelog descriptions naturally (e.g., '更新了 1 个模型文件' -> 'updated 1 model file')."
    )
    
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0.1
    )
    return response.choices[0].message.content.strip()

def extract_non_header_body(content):
    """去除首行的语言跳转链接，只获取正文内容"""
    lines = content.splitlines()
    if lines and ("[中文]" in lines[0] or "[English]" in lines[0]):
        return "\n".join(lines[1:]).lstrip()
    return content

def process():
    # 依赖与 API key 在运行时检查（避免模块 import 时直接 exit 的旧行为）
    if OpenAI is None:
        print("Warning: 缺少 'openai' 库，跳过 AI 翻译（pip install openai）。")
        return
    api_key = (os.getenv("LLM_API_KEY")
               or os.getenv("DEEPSEEK_API_KEY")
               or os.getenv("OPENAI_API_KEY"))
    if not api_key:
        print("Warning: No API key provided. Skipping AI translation step.")
        return
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    client = OpenAI(api_key=api_key, base_url=base_url)

    if not ZH_README.is_file():
        print(f"Error: {ZH_README} not found.")
        return

    zh_full = ZH_README.read_text(encoding="utf-8")

    en_full = ""
    if EN_README.is_file():
        en_full = EN_README.read_text(encoding="utf-8")

    # 1. 提取英文版首行的语言切换链接（保护头部不被篡改）
    en_lines = en_full.splitlines()
    header_line = en_lines[0] if (en_lines and "[中文]" in en_lines[0]) else '[中文](https://github.com/nekohalawrence/YSM-Model-Author/blob/main/README.md), English'

    # 2. 提取并保留英文版的 AUTHORS_LIST 自动化区域
    en_authors_block = ""
    if AUTHORS_START in en_full and AUTHORS_END in en_full:
        en_authors_block = AUTHORS_START + en_full.split(AUTHORS_START, 1)[1].split(AUTHORS_END, 1)[0] + AUTHORS_END

    # 3. 对中文文档剔除 AUTHORS_LIST（替换为占位符），准备检测增量
    zh_body = extract_non_header_body(zh_full)
    
    if AUTHORS_START in zh_body and AUTHORS_END in zh_body:
        zh_body = re.sub(
            f"{AUTHORS_START}.*?{AUTHORS_END}", 
            "__AUTHORS_LIST_PLACEHOLDER__", 
            zh_body, 
            flags=re.DOTALL
        )

    # 4. 判断是否有需要增量翻译的内容
    # 获取英文正文（去除头部和 AUTHORS_LIST 占位符后）
    en_body_clean = extract_non_header_body(en_full)
    if AUTHORS_START in en_body_clean and AUTHORS_END in en_body_clean:
        en_body_clean = re.sub(
            f"{AUTHORS_START}.*?{AUTHORS_END}", 
            "__AUTHORS_LIST_PLACEHOLDER__", 
            en_body_clean, 
            flags=re.DOTALL
        )

    # 如果除开自动生成区域外，主体没有任何变化，则直接跳过翻译 API 调用
    if zh_body.strip() == en_body_clean.strip():
        print("No changes detected in README.md content. Skipping translation.")
        return

    print("Changes detected in README.md. Performing AI translation...")

    # 5. 调用 AI 对更新后的正文进行整体翻译
    translated_body = call_llm_translate(zh_body, client, model)

    # 6. 还原 AUTHORS_LIST 自动生成的英文表格数据
    if "__AUTHORS_LIST_PLACEHOLDER__" in translated_body:
        translated_body = translated_body.replace("__AUTHORS_LIST_PLACEHOLDER__", en_authors_block)

    # 7. 重新组装文档：保留头部跳转 + 翻译后的正文
    final_en_content = f"{header_line}\n\n{translated_body.strip()}\n"

    EN_README.write_text(final_en_content, encoding="utf-8")

    print("README-EN.md successfully updated with translated changes.")

if __name__ == "__main__":
    process()