# -*- coding: utf-8 -*-
"""作品大类分类：从 character/*.json 现算分类 + 根 README 模型分类区块渲染。

数据流（character/*.json 为权威源，不再从根 README.md 读取，也不落盘 category_map.json）：
    character/<作品>.json（含可选 category 字段：字符串或数组，数组表示作品属于多个大类）
      -> build_category_map 现算 {大类: [作品键...]}（供 README 区块 / 模型标签用，不落盘）
      -> render_readme_works_section 渲染根 README 的"模型分类"区块（展示产物，非数据源）
      -> generate_model_readmes 的 Category / Game 标签

"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# 大类（Anime 细分为 动画/漫画/小说；作品 category 可为字符串或数组，数组=多分类）
CATEGORIES = ["Game", "Anime", "Manga", "Novel", "Music", "Original", "Other"]

# 根 README"模型分类"区块的包裹标记（author_index 作者表同款 marker 方案）
README_START_MARKER = "<!-- WORKS_CATEGORY_START -->"
README_END_MARKER = "<!-- WORKS_CATEGORY_END -->"

# 根 README 区块的大类标题
CATEGORY_TITLES = {
    "Game": "## 游戏",
    "Anime": "## 动画",
    "Manga": "## 漫画",
    "Novel": "## 小说",
    "Music": "## 音乐",
    "Original": "## 原创",
    "Other": "## 其他",
}

CATEGORY_TITLES_EN = {
    "Game": "## Games",
    "Anime": "## Anime",
    "Manga": "## Manga",
    "Novel": "## Novels",
    "Music": "## Music",
    "Original": "## Original",
    "Other": "## Other",
}


def build_category_map(data: dict, default: str = "Other") -> dict[str, list[str]]:
    """从 character/*.json 现算 {大类: [作品键...]}（按 CATEGORIES 顺序、键字母序）。

    作品条目（dict）的 category 字段决定大类：字符串=单一分类，数组=多个分类
    （作品会同时出现在多个大类下）；缺失/空时归 default（默认 Other）。
    不再落盘 category_map.json，调用方（README 区块 / 模型标签 / audit）直接从
    character/*.json 现算，保证单一数据源。
    """
    cat_map: dict[str, list[str]] = {}
    for k, v in (data.get("works") or {}).items():
        cat = v.get("category") if isinstance(v, dict) else None
        if isinstance(cat, str):
            cats = [cat] if cat else [default]
        elif isinstance(cat, list):
            cats = [c for c in cat if c] or [default]
        else:
            cats = [default]
        for c in cats:
            cat_map.setdefault(c, []).append(k)
    return {c: sorted(cat_map.get(c, [])) for c in CATEGORIES if c in cat_map}


# ---------------------------------------------------------------------------
# 根 README"模型分类"区块渲染（展示产物，从 works.json 自动生成）
# ---------------------------------------------------------------------------
def _work_names(v, lang: str) -> list[str]:
    if isinstance(v, dict):
        lst = v.get(lang) or []
        return lst if isinstance(lst, list) else [lst]
    return v if isinstance(v, list) else ([v] if v else [])


def render_readme_works_section(data: dict,
                                model_count_map: dict | None = None,
                                lang: str = 'zh') -> str:
    """渲染根 README 的模型分类区块（含 <details>，不含包裹 marker）。

    lang='zh' 输出中文标题/表头，lang='en' 输出英文。model_count_map 为
    {作品键小写: 模型数}（由 count_models_by_work 扫描模型目录得出），缺省时 0。
    """
    is_en = lang == 'en'
    cat_map = build_category_map(data)
    works = data.get("works") or {}
    mcounts = model_count_map or {}
    total_models = sum(mcounts.values())
    total_works = len(works)
    if is_en:
        summary = "Model Categories"
        stats = f"> Total {total_works} works, {total_models} models"
        cat_titles = CATEGORY_TITLES_EN
        table_header = "| Key | English Name | Chinese Name | Model Count |"
        works_label = "works"
        models_label = "models"
    else:
        summary = "模型分类"
        stats = f"> 共 {total_works} 个作品、{total_models} 个模型"
        cat_titles = CATEGORY_TITLES
        table_header = "| 作品键 | 英文名 | 中文名 | 模型数 |"
        works_label = "作品"
        models_label = "模型"
    lines = [
        "<details>", "",
        f"<summary>{summary}</summary>", "",
        stats
    ]
    for cat in CATEGORIES:
        keys = cat_map.get(cat) or []
        if not keys:
            continue
        cat_models = sum(mcounts.get(k.lower(), 0) for k in keys)
        if is_en:
            lines.append(f"{cat_titles[cat]} ({len(keys)} {works_label} · {cat_models} {models_label})")
        else:
            lines.append(f"{cat_titles[cat]}（{len(keys)} {works_label} · {cat_models} {models_label}）")
        lines.append("")
        lines.append(table_header)
        lines.append("| --- | --- | --- | ---: |")
        for k in sorted(keys):
            v = works.get(k) or {}
            en_names = _work_names(v, "en")
            en = en_names[0] if en_names else ""
            zh_names = _work_names(v, "zh")
            cn = zh_names[0] if zh_names else ""
            lines.append(f"| {k} | {en} | {cn} | {mcounts.get(k.lower(), 0)} |")
        lines.append("")
    lines.append("</details>")
    return "\n".join(lines).rstrip() + "\n"


def _iter_model_dirs(d: Path):
    """递归收集模型目录：目录含 .ysm 文件或 previews/ 即视为模型目录（同模型 README 规则）。"""
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    has_ysm = any(e.is_file() and e.suffix.lower() == ".ysm" for e in entries)
    has_previews = any(e.is_dir() and e.name == "previews" for e in entries)
    if has_ysm or has_previews:
        yield d
        return
    for e in entries:
        if e.is_dir() and e.name != "previews" and not e.name.startswith("."):
            yield from _iter_model_dirs(e)


def count_models_by_work(model_roots: list[Path]) -> dict[str, int]:
    """扫描模型目录，按文件夹名前缀（作品键，小写）统计模型数。

    前缀取模型文件夹名 `_` 前段（如 AK_嵯峨 -> 'ak'）；与模型 README 收集同规则
    （含 .ysm 或 previews/ 的目录视为模型目录，递归）。用于作品说明表格的模型数列。
    """
    counts: dict[str, int] = {}
    for root in model_roots:
        if not root.is_dir():
            continue
        for md in _iter_model_dirs(root):
            prefix = md.name.split("_")[0].strip().lower()
            if prefix:
                counts[prefix] = counts.get(prefix, 0) + 1
    return counts


def update_readme_works_section(readme_path: Path, data: dict,
                                model_count_map: dict | None = None,
                                lang: str = 'zh') -> tuple[bool, str]:
    """把渲染好的模型分类区块写入根 README。返回 (是否变更, 动作)。

    lang 参数透传给 render_readme_works_section（'zh' 中文 / 'en' 英文）。
    优先替换已有 marker 包裹的区块；无 marker 时替换旧版手写的
    `<details><summary>模型分类/Model Categories</summary>...</details>` 整块；
    均无则追加到末尾。
    """
    section = render_readme_works_section(data, model_count_map, lang=lang)
    full = f"{README_START_MARKER}\n{section}{README_END_MARKER}"
    content = (readme_path.read_text(encoding="utf-8", errors="ignore")
               if readme_path.exists() else "")
    # 1) 已有 marker：替换 marker 之间内容
    if README_START_MARKER in content and README_END_MARKER in content:
        new = re.sub(re.escape(README_START_MARKER) + r".*?"
                     + re.escape(README_END_MARKER), full, content, flags=re.DOTALL)
        if new == content:
            return False, ""
        readme_path.write_text(new, encoding="utf-8")
        return True, "updated"
    # 2) 无 marker 但有旧版手写"模型分类"/"Model Categories"<details> 块：整块替换
    old_re = re.compile(
        r"<details>\s*\n\s*<summary>\s*(?:模型分类|Model Categories)\s*</summary>.*?</details>",
        re.DOTALL)
    new_content, n = old_re.subn(full + "\n", content, count=1)
    if n:
        if new_content != content:
            readme_path.write_text(new_content, encoding="utf-8")
            return True, "replaced-old-block"
        return False, ""
    # 3) 追加到文件末尾
    sep = "\n" if content and not content.endswith("\n") else ""
    readme_path.write_text(content + sep + full + "\n", encoding="utf-8")
    return True, "appended"


def get_work_entry(works: dict, prefix: str):
    """按作品前缀（文件夹名 _ 前段）匹配 works 条目（大小写不敏感）。未命中返回 None。"""
    key = prefix.strip().lower()
    for k, v in works.items():
        if k.lower() == key:
            return v
    return None


def get_work_tags(works: dict, prefix: str) -> str:
    """作品标签（模型 README 的 Game 字段）：简写 + 中英文标准名 -> '#名称' 空格连接。

    只取 name 键下中文、英文的**第一个标准名**（abbr 已覆盖简写），不包含日语名
    与别名/缩写（避免与 abbr 重复，如 en 第二项是缩写本身）。
    """
    v = get_work_entry(works, prefix)
    if v is None:
        return "#Unknown"
    abbr = str(v.get('abbr') or '').strip()
    names = ([abbr] if abbr else [])
    names += _work_names(v, "en")[:1] + _work_names(v, "zh")[:1]
    # 去重保序（abbr 可能已含在 en/aliases 里）
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        n = str(n).strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return " ".join(f"#{n}" for n in out) or "#Unknown"
