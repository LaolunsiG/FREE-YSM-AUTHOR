# -*- coding: utf-8 -*-
"""作者 README 生成共享逻辑：按 authors.json 数据渲染作者 README。

作者 README 直接由集中作者数据（author-info/authors.json）生成：
  # <编号> + ## Author（Name + 平台分类段）+ ## Models（模型列表，按作品分组折叠）。
不含 Role——作者在不同模型里负责的功能不一致，角色只记录在模型级
（co_creators.json / .ysm 作者块），作者级不再固定 Role。

被 03_generate_author_readmes.py（生成作者 README）与 01_organize_models.py
（归档登记作者名）共用。抽到 lib/ 避免跨脚本 import 含 `&` 等非法字符的文件名。
"""
from __future__ import annotations

import re
from pathlib import Path

from lib import paths as lib_paths
from lib import ysm as lib_ysm

# 平台分类输出顺序（与模型 README 的 author_block 模板一致）
PLATFORM_ORDER = ['SocialPlatform', 'SupportPlatform', 'OtherPlatform', 'GroupChat']

# ---- 作者标签（词表 tag_labels.json 驱动；根 README 与作者 README 共用） ----


def tag_order_key(k: str) -> tuple:
    """标签排序键：按词表 order（小在前）；未收录/无 order 的新标签排最后。"""
    meta = load_tag_labels().get(k) or {}
    order = meta.get('order')
    return (0, order) if isinstance(order, int) else (1, 0)


_TAG_LABELS_CACHE: dict | None = None


def load_tag_labels() -> dict:
    """加载标签词表（author-info/tag_labels.json）：{键: {zh, en, category}}。

    懒加载并缓存；词表缺失时返回 {}（渲染退化为 emoji 兑底）。
    词表由用户维护，新增标签在其中登记中文/英文名后即可显示。
    """
    global _TAG_LABELS_CACHE
    if _TAG_LABELS_CACHE is None:
        p = lib_paths.data_path('author-info', 'tag_labels.json')
        _TAG_LABELS_CACHE = lib_paths.load_json(p, {}) or {}
    return _TAG_LABELS_CACHE


def format_tag(meta: dict) -> str:
    """词表条目 -> '中文/English' 显示串（en 缺失时只用中文）。"""
    zh = str(meta.get('zh') or '').strip()
    en = str(meta.get('en') or '').strip()
    return f'{zh}/{en}' if zh and en else (zh or en)


def compute_author_marks(entry: dict) -> list[str]:
    """作者徽章键列表：由 authors.json 的 badges（词表登记）+ team 生成。

    纯 badges 驱动，无自动判定——自动判定的徽章（高产≥20 模型、目录名含 nsfw/r18）
    由 03_generate_author_readmes.py 生成作者 README 时经 auto_marks 追加。
    根 README 只用本函数返回值（仅显示词表里有 emoji 的徽章）。
    """
    badges = [str(b).lower() for b in (entry.get('badges') or []) if isinstance(b, str)]
    marks = list(dict.fromkeys(badges))   # 去重保序
    if entry.get('team'):
        marks.append('team')
    return marks


def format_author_name(authors_str: str) -> str:
    """'鸡姬(raw_chicken)' -> '#鸡姬 | #raw_chicken'（保留原始顺序，每个别名加 #）。

    与 organize_models 原实现一致：按分隔符拆段，'中文(English)' 括号对拆成两个别名。
    """
    tags: list[str] = []
    for seg in re.split(r'[\s|｜,，、;/；]+', authors_str):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r'^([^()（）]*)[(（]([^)）]*)[)）]$', seg)
        if m:
            outer, inner = m.group(1).strip(), m.group(2).strip()
            parts = [outer, inner] if outer and inner else [outer or inner]
        else:
            parts = [seg]
        for tag in parts:
            tag = tag.strip()
            if tag and not tag.startswith('#') and not tag.startswith('＃'):
                tag = '#' + tag
            if tag and tag not in tags:
                tags.append(tag)
    return ' | '.join(tags)


def _classify_platforms(platforms: dict,
                        platform_map: dict) -> dict[str, list[tuple[str, str]]]:
    """把扁平 {平台键: 值} 按 platform_map 分类为 {分类: [(规范平台名, 值)]}。

    键与别名（小写）反查归属，规范名本身也参与匹配；未命中归 OtherPlatform。
    """
    reverse: dict[str, tuple[str, str]] = {}
    for field, pmap in platform_map.items():
        for canonical, aliases in pmap.items():
            for alias in [canonical, *aliases]:
                reverse.setdefault(alias.strip().lower(), (field, canonical))
    out: dict[str, list[tuple[str, str]]] = {}
    for key, value in platforms.items():
        hit = reverse.get(key.strip().lower())
        field, canonical = hit if hit else ('OtherPlatform', key.strip())
        out.setdefault(field, []).append((canonical, str(value)))
    return out


def load_work_names() -> dict[str, dict]:
    """读 character/*.json 构建 {作品键: {'zh': 中文名, 'en': 英文名}}。

    英文名可能缺失（如 OC/部分动漫作品只有中文名），调用方需回退处理。
    """
    rdir = lib_paths.data_path('model-info', 'character')
    out: dict[str, dict] = {}
    if rdir.is_dir():
        for f in sorted(rdir.glob('*.json')):
            content = lib_paths.load_json(f, {})
            if not isinstance(content, dict):
                continue
            work = content.get('work')
            if not isinstance(work, dict):
                continue
            abbr = work.get('abbr') or work.get('name') or ''
            name_map = work.get('name') or {}

            def _first(v):
                """name 值可能是字符串（旧）或数组（新，首项=标准名）。"""
                if isinstance(v, list):
                    return v[0] if v else ''
                return v if isinstance(v, str) else ''

            zh = _first(name_map.get('zh')) if isinstance(name_map, dict) else _first(name_map)
            en = _first(name_map.get('en')) if isinstance(name_map, dict) else ''
            if abbr and (zh or en):
                out[str(abbr)] = {'zh': str(zh) if zh else '',
                                  'en': str(en) if en else ''}
    return out


def render_models_section(models: list[str], work_names: dict[str, str],
                          links: list[str] | None = None) -> str:
    """渲染 ## Models 段：按作品字母序分组（Unknown 最后），每组 <details> 折叠。

    作品前缀取模型文件夹名第一个 '_' 前部分；无前缀或 Unknown_ 归 Unknown。
    links 提供时用于链接目标（如相对路径），models 仅作显示名与分组键；缺省同名
    （作者 README 场景 links=None）。顶层 Other-YSM-Models 索引用它做路径链接。
    """
    if links is None:
        links = models
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, link in zip(models, links):
        prefix = name.split('_', 1)[0].strip() if '_' in name else 'Unknown'
        if not prefix or prefix.lower() == 'unknown':
            prefix = 'Unknown'
        groups.setdefault(prefix, []).append((name, link))

    ordered = sorted(groups, key=lambda k: (k.lower() == 'unknown', k.lower()))
    lines = ['## Models', '']
    for prefix in ordered:
        items = groups[prefix]
        # Unknown 本身即"未知"，不再叠加完整名；其余作品查 character/*.json 的中英文名
        info = {} if prefix.lower() == 'unknown' else work_names.get(prefix, {})
        zh = info.get('zh', '') if isinstance(info, dict) else ''
        en = info.get('en', '') if isinstance(info, dict) else ''
        if zh and en:
            title = f'{en} | {zh}（{len(items)}）'
        elif zh:
            title = f'{zh}（{len(items)}）'
        elif en:
            title = f'{en}（{len(items)}）'
        else:
            title = f'{prefix}（{len(items)}）'
        lines.append('<details>')
        lines.append(f'<summary><b>{title}</b></summary>')
        lines.append('')
        for n, ln in items:
            lines.append(f'- [{n}]({ln})')
        lines.append('')
        lines.append('</details>')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def render_author_readme(author_id: str, entry: dict,
                         models: list[str] | None = None,
                         author_dir: Path | None = None,
                         auto_marks: list[str] | None = None) -> str:
    """按 authors.json 的 entry 生成作者 README（Name + team + 徽章 + 平台段 + 可选 Models 段）。

    entry: {'name': [...], 'platforms': {...}, 'badges': [...], 'team': ...}（authors.json 作者条目）。
    models: 模型文件夹名列表（非空时渲染 ## Models 段，按作品分组折叠）。
    author_dir: 作者目录（保留参数，当前未用）。
    auto_marks: 自动判定的徽章键（高产/18禁），由 03_generate_author_readmes.py 计算传入，
                追加显示在 **badges**: 行（不进 authors.json）。
    """
    names = entry.get('name') or []
    if isinstance(names, str):
        names = [names]
    names = [str(n) for n in names if str(n)]
    # 每个名称加 # 前缀（已有 #/＃ 不重复）；README 显示与模型文件夹标签风格一致
    name_str = ' | '.join(
        (n if n.startswith('#') or n.startswith('＃') else '#' + n)
        for n in names
    ) if names else '暂无'

    lines = [f'# {author_id}', '', '## Author', '', f'- **Name**: {name_str}']
    # 团队名（手动维护于 authors.json 的 team 键，有值才显示）
    team = str(entry.get('team') or '').strip()
    if team:
        lines.append(f'- **Team**: {team}')
    # 标签（词表驱动；英文一组 | 中文一组，先英后中；team 由独立行展示）
    marks = [m for m in dict.fromkeys(compute_author_marks(entry) + (auto_marks or []))
             if m != 'team']
    if marks:
        labels = load_tag_labels()
        en_parts: list[str] = []
        zh_parts: list[str] = []
        for m in sorted(marks, key=tag_order_key):
            meta = labels.get(m) or {}
            en = str(meta.get('en') or '').strip()
            zh = str(meta.get('zh') or '').strip()
            # 先英后中分组；自动补全缺失翻译（缺英文用中文/键名，缺中文用英文/键名）
            en_parts.append('#' + (en or zh or m))
            zh_parts.append('#' + (zh or en or m))
        tag_str = ' '.join(en_parts) + (' | ' + ' '.join(zh_parts) if zh_parts else '')
        lines.append(f'- **Badges**: {tag_str}')
    classified = _classify_platforms(entry.get('platforms') or {},
                                     lib_ysm.load_platform_map())
    for field in PLATFORM_ORDER:
        pairs = classified.get(field) or []
        if not pairs:
            continue
        tags = ' #'.join(key for key, _ in pairs)
        lines.append(f'- **{field}**: #{tags}')
        for key, value in pairs:
            # 平台值直接呈现纯文本（链接为纯 URL，与 authors.json 存储一致），
            # 不用 [名字](url) 包裹
            lines.append(f'  - **{key}**: {value}')

    if models:
        lines.append('')
        lines.append(render_models_section(models, load_work_names()).rstrip('\n'))
    return '\n'.join(lines) + '\n'
