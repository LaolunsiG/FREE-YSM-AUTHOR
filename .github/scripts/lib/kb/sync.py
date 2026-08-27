# -*- coding: utf-8 -*-
"""kb works 索引（原 kb_tool.py 的 sync 部分）。

character/*.json 为作品数据权威源（--add-work / --suggest-works 维护，不再从 README 同步）；
build_work_index 把 works 派生为「作品名 -> 键」映射写入 parse.EXTRA_WORK_ALIASES
（供 resolve_name 的前缀识别使用）。

sync_works_from_readme / parse_readme_works 为旧版「README 为作品权威源」的同步逻辑，
已废弃（仅保留定义以便外部旧引用兼容），新流程不再调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib.kb import parse  # noqa: E402
from lib.kb.text import normalize_work_name  # noqa: E402


def parse_readme_works(readme_path: Path) -> list[dict]:
    """解析 README 作品表。

    每行格式：英文名称[,别名...] | 中文名称[,别名...] | 日文名称
    跳过分类标题（无 |）和 Markdown 表格（行首 |）。
    """
    if not readme_path.exists():
        return []
    works: list[dict] = []
    for line in readme_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("|") or "|" not in line:
            continue
        # 剥离 Markdown 无序列表标记（- 名称 | ...）
        for marker in ("- ", "* ", "+ "):
            if line.startswith(marker):
                line = line[len(marker):].strip()
                break
        parts = [p.strip() for p in line.split("|")]
        en_names = [n.strip() for n in parts[0].split(",") if n.strip()]
        if not en_names:
            continue
        cn_names = [n.strip() for n in parts[1].split(",") if n.strip()] if len(parts) > 1 else []
        ja_names = [n.strip() for n in parts[2].split(",") if n.strip()] if len(parts) > 2 else []
        works.append({"en": en_names, "zh": cn_names, "ja": ja_names})
    return works


def work_value_names(v) -> list[str]:
    """从 works 值（dict/列表/字符串）提取全部名称。"""
    names: list[str] = []
    if isinstance(v, dict):
        for lst in v.values():
            if isinstance(lst, list):
                names.extend(lst)
            elif lst:
                names.append(lst)
    elif isinstance(v, list):
        names.extend(v)
    elif v:
        names.append(v)
    return names


def _dedup(items: list[str]) -> list[str]:
    """去空、去重（保序）。"""
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def sync_works_from_readme(data: dict, readme_path: Path) -> tuple[int, int]:
    """README 为 works 权威源：同步新增/更新。

    键规则：英文名列表的最后一项作为作品键（用户约定的默认缩写）；
    若与现有 works 任一名称匹配则复用现有键。README 提供的语言键覆盖，
    未提供的语言键（如现有 ko 等）保留。
    返回 (新增数, 更新数)。
    """
    parsed = parse_readme_works(readme_path)
    if not parsed:
        return 0, 0
    works = data.setdefault("works", {})
    idx: dict[str, str] = {}
    for wk, v in works.items():
        for name in work_value_names(v):
            idx.setdefault(normalize_work_name(name), wk)
        idx.setdefault(normalize_work_name(wk), wk)
    added = updated = 0
    for p in parsed:
        norm_all = [normalize_work_name(n) for n in p["en"] + p["zh"]]
        key = next((idx[n] for n in norm_all if n and n in idx), None)
        if key is None:
            key = p["en"][-1]  # 新作品：英文名最后一项作为默认键
            added += 1
        else:
            updated += 1
        new_val: dict = {}
        if key in works and isinstance(works[key], dict):
            new_val = dict(works[key])  # 保留现有其它语言键
        if p["en"]:
            new_val["en"] = _dedup(p["en"])
        if p["zh"]:
            new_val["zh"] = _dedup(p["zh"])
        if p["ja"]:
            new_val["ja"] = _dedup(p["ja"])
        works[key] = new_val
    return added, updated


def build_work_index(data: dict) -> None:
    """从 works 数据构建全局作品名 -> 键 映射（解析前缀时使用）。

    work 值是扁平 dict（en/zh/ja/category 等）；category 是分类字段、不是作品名，
    必须排除（否则 'Other' 会被误当 Animal 作品的别名）。
    """
    aliases: dict[str, str] = {}
    for wk, v in (data.get("works") or {}).items():
        if isinstance(v, dict):
            names: list = []
            for k2, v2 in v.items():
                if k2 == "category":
                    continue
                if isinstance(v2, list):
                    names.extend(v2)
                elif v2:
                    names.append(v2)
        else:
            names = work_value_names(v)
        for name in names:
            norm = normalize_work_name(str(name))
            if norm:
                aliases.setdefault(norm, wk)
        norm = normalize_work_name(wk)
        if norm:
            aliases.setdefault(norm, wk)
    parse.set_work_aliases(aliases)
