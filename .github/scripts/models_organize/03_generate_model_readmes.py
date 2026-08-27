#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为所有模型目录生成标准化的英文模型 README（不要求存在预览图）。

数据（外置，可手工维护，位于 .github/data/）：
  author-info/authors.json         作者集中数据（name 数组 / platforms），author_index.py --data 生成
  author-info/platform_map.json    平台分类映射 {分类: [平台键...]}（分类为键、平台键列表为值）
  author-info/co_creators.json     模型 -> co-creator 作者列表（按需生成，无记录时文件不存在）
  model-info/character/*.json      作品知识库（合并格式：作品键 -> 多语言名称 + category + roles）
  templates/model_readme.template.json  模型 README 结构模板（由 _Template/ 转化）

模型 README 结构（按模板渲染）：
  # <模型名>
  ## Model Details（<details> 内）
    - **Category**: 大类标签（从 character/*.json 的 category 现算，支持多分类）
    - **Game**: 作品标签（character/*.json，自动生成；不再读取根 README 分类区块）
    ## Author Details
      Name / 平台分类段（authors.json + platform_map 分类）
    ## Co-creator Details
      同 Author 结构；数据来自 co_creators.json，无记录时解析 .ysm 兜底
  ## Preview Images（独立 <details open>）
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import quote
# 脚本按流程阶段分类到 scripts/<类别>/ 子目录：把 .github/scripts 加回 sys.path，
# 保证 lib/ 与跨分类脚本可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from lib import paths as lib_paths
from lib import readme as lib_readme
from lib import models as lib_models
from lib import previews as lib_previews
from lib import terms as lib_terms
from lib import ysm as lib_ysm
from lib.kb.category import get_work_tags
from lib.kb.storage import load_kb_json
from lib.kb.sync import build_work_index
from lib.kb.cmds import build_indexes
from lib.kb.parse2 import resolve_name3

WORKSPACE_ROOT = lib_paths.WORKSPACE_ROOT
MAIN_README_PATH = WORKSPACE_ROOT / 'README.md'

ROOT_DIRS = [
    WORKSPACE_ROOT,
    # 兼容旧目录（若存在）
    WORKSPACE_ROOT / 'Blockbench-Models',
    WORKSPACE_ROOT / 'Other-YSM-Models',
]
IMAGE_EXTS = lib_previews.IMAGE_EXTS
PREVIEW_MARKER = lib_previews.PREVIEW_MARKER
START_MARKER = '<!-- GENERATED MODEL PREVIEW README START -->'
END_MARKER = '<!-- GENERATED MODEL PREVIEW README END -->'
# Download 直链基址：raw.githubusercontent.com/<owner>/<repo>/<默认分支>。
# 换仓库/分支时只需改这一处（文件名 percent-encoding 由 build_download_lines 处理）。
RAW_BASE = 'https://raw.githubusercontent.com/nekohalawrence/YSM-Model-Author/main'
# Download 区块收录的文件后缀（小写）：模型本体 .ysm 或压缩包等打包结构。
# 白名单避免把 README、说明 txt、预览图等无关文件也列进下载列表。
DOWNLOAD_EXTS = frozenset({
    '.ysm', '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.jar', '.mcpack',
})

# 模板与数据（惰性加载 + 模块级缓存，全量扫描只读一次）
_TEMPLATE: dict = {}
_CATEGORY_MAP: dict[str, list[str]] = {}
_PLATFORM_MAP: dict | None = None
_CO_CREATORS: dict | None = None
_WORKS: dict = {}
# 回退解析发现、但 authors.json 中不存在的作者（aid -> {name, platforms}），
# --apply 时合并写回 authors.json（dry-run 只打印预览）
_NEW_AUTHORS: dict[str, dict] = {}


def load_template() -> dict:
    """读取模型 README 模板（.github/data/templates/model_readme.template.json）并缓存。"""
    if not _TEMPLATE:
        data = lib_paths.load_json(
            lib_paths.data_path('templates', 'model_readme.template.json'), {})
        _TEMPLATE.update(data)
    return _TEMPLATE


def load_work_categories() -> dict[str, list[str]]:
    """从 character/*.json 现算 作品缩写(小写) -> 大类列表（不再读 category_map.json）。

    category 支持字符串（单分类）或数组（多分类）；缺失/空归 Other。
    """
    if not _CATEGORY_MAP:
        for key, v in load_works().items():
            if not isinstance(v, dict):
                continue
            cats = v.get("category") or ["Other"]
            if isinstance(cats, str):
                cats = [cats]
            _CATEGORY_MAP[key.lower()] = [str(c) for c in cats if c] or ["Other"]
    return _CATEGORY_MAP


def load_works() -> dict:
    """读取作品表并缓存；Game 标签据此自动生成（不再读 README 分类）。

    作品元数据已并入 character/<作品>.json（合并后格式），从各作品文件的
    顶层键 + 元数据聚合出 {作品键 -> 条目}（与 lib.kb 的 data['works'] 同构）。
    """
    if not _WORKS:
        works: dict = {}
        rdir = lib_paths.data_path('model-info', 'character')
        if rdir.is_dir():
            for f in sorted(rdir.glob('*.json')):
                content = lib_paths.load_json(f, {})
                if not isinstance(content, dict):
                    continue
                # 新格式：作品键由 work.abbr 决定（读取不依赖文件名）
                work = content.get('work')
                if not isinstance(work, dict):
                    continue
                abbr = work.get('abbr') or work.get('name')
                if not abbr:
                    continue
                meta: dict = {}
                name_map = work.get('name') or {}
                aliases_map = work.get('aliases') or {}
                for lang in ('en', 'zh', 'ja'):
                    arr: list[str] = []
                    nm = name_map.get(lang) if isinstance(name_map, dict) else None
                    if isinstance(nm, list):
                        # name 值可能是数组（如 en: ["...", "缩写"]）：逐个并入，
                        # 不做 str() 整体转换（否则变成 "['...', '...']" 的列表 repr）
                        for x in nm:
                            if x is not None and str(x) not in arr:
                                arr.append(str(x))
                    elif nm:
                        arr.append(str(nm))
                    for a in ((aliases_map.get(lang) or []) if isinstance(aliases_map, dict) else []):
                        if a and str(a) not in arr:
                            arr.append(str(a))
                    meta[lang] = arr
                # 作品缩写（作品键，如 AK）供 Game 标签简写在前
                meta['abbr'] = str(abbr)
                if work.get('category') is not None:
                    meta['category'] = work['category']
                if any(meta.get(fd) for fd in ('en', 'zh', 'ja', 'category')):
                    works[str(abbr)] = meta
        _WORKS.update(works)
    return _WORKS


def get_platform_map() -> dict:
    """读取平台分类映射（lib/ysm 实现，反查用）并缓存。"""
    global _PLATFORM_MAP
    if _PLATFORM_MAP is None:
        _PLATFORM_MAP = lib_ysm.load_platform_map()
    return _PLATFORM_MAP


def get_category_tag(model_folder_name: str, work_category_map: dict[str, list[str]]) -> str:
    """按模型文件夹前缀查作品大类（从 character/*.json 现算）；未命中返回 #Unknown。

    作品可有多个大类（如 ["Anime", "Manga", "Novel"]），生成多个 #标签空格连接。
    """
    prefix = model_folder_name.split('_')[0].strip().lower()
    cats = work_category_map.get(prefix) or []
    if not cats:
        return "#Unknown"
    return " ".join(f"#{c}" for c in cats)


def get_author_info(model_dir: Path) -> tuple[str, dict]:
    """返回 (author_id, 作者信息 {name, platforms})。

    优先集中数据 authors.json；缺失时回退解析作者 README。
    作者目录在仓库根，命名 '<编号>-<作者名[,别名]>'（编号 = 目录名前 4 位）；
    非作者目录（Blockbench/Other-YSM 根）返回空信息。
    """
    author_dir = model_dir.parent
    if not author_dir.is_dir() or not is_author_dir_name(author_dir.name):
        return '', {'name': [], 'platforms': {}}

    author_id = author_dir.name[:4]
    authors = lib_readme.load_authors_index().get('authors') or {}
    entry = authors.get(author_id)
    if entry:
        return author_id, entry

    # 回退：作者 README 未收录时现场解析
    for candidate in ['README.md', 'readme.md', 'Readme.md']:
        candidate_path = author_dir / candidate
        if candidate_path.is_file():
            content = candidate_path.read_text(encoding='utf-8', errors='ignore')
            info = {
                'name': lib_readme.split_author_names(lib_readme.parse_author_name_value(content)),
                'platforms': lib_readme.extract_platforms(content),
            }
            # 作者不在 authors.json：暂存解析结果，--apply 时合并写回
            if info.get('name'):
                _NEW_AUTHORS[author_id] = info
            return author_id, info
    return author_id, {'name': [], 'platforms': {}}


_ROLE_PARSE: dict | None = None


def get_role_parser() -> tuple[list, dict, dict]:
    """惰性构建 resolve_name3 需要的角色索引（全量 1400+ 模型只构建一次）。

    返回 (roles, en_to_cn, cn_to_en)，供模型文件夹名解析角色名（Name 字段）。
    """
    global _ROLE_PARSE
    if _ROLE_PARSE is None:
        kb = load_kb_json(lib_paths.MODEL_INFO_DIR)
        build_work_index(kb)
        roles = list(kb.get('roles') or [])
        _c, _e, e2c, c2e = build_indexes(roles)
        _ROLE_PARSE = (roles, e2c, c2e)
    return _ROLE_PARSE


def get_main_author_role(model_dir: Path) -> str:
    """从模型 .ysm 主作者块取 role（lib_ysm.classify_authors 三级信号判定的 primary 块，
    与 01_organize_models 的归档分类一致）；无 .ysm 返回空。

    模型 README 的 Author Role 以模型内容（.ysm）为准，authors.json 无 role 时用它；
    .ysm 也没有 role 时由渲染层回退到模板默认值。
    """
    ysm_files = sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM'))
    for f in ysm_files:
        meta = lib_ysm.extract_metadata(f, quiet=True)
        blocks = meta.get('author_blocks') or []
        primary, _, _ = lib_ysm.classify_authors(blocks)
        if primary:
            return primary.get('role') or ''
    return ''


def collect_preview_images(model_dir: Path) -> list[Path]:
    """收集模型目录下的预览图（复用 lib/previews.py 统一规则）"""
    return lib_previews.collect_preview_images(model_dir)


# ---------------------------------------------------------------------------
# co-creator 数据（co_creators.json 优先，.ysm 解析兜底）
# ---------------------------------------------------------------------------
def load_co_creators() -> dict:
    """读取 co-creator 元数据（author-info/co_creators.json），惰性缓存——
    全量扫描 1400+ 模型时避免每次调用都重复读文件。"""
    global _CO_CREATORS
    if _CO_CREATORS is None:
        _CO_CREATORS = lib_paths.load_json(lib_paths.data_path('author-info', 'co_creators.json'), {})
    return _CO_CREATORS


def same_model(a: str, b: str) -> bool:
    """判断两个名称是否属于同一模型（复用 lib/models.py 统一容错匹配）"""
    return lib_models.same_model(a, b)


def get_model_authors(model_dir: Path, primary_entry: dict,
                      primary_id: str) -> list[dict]:
    """从 .ysm 获取所有模型作者（model_blocks），返回列表用于渲染 Author 区。

    第一项为主作者（合并 primary_entry 的 authors.json 数据），后续项为
    其他模型作者（从 .ysm 原始块提取）；空名块自动过滤。

    各 dict 结构与 render_person_block 兼容：
      {'name': [str], 'role': str, 'platforms': {...}, 'author_id': str}
    """
    ysm_files = sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM'))
    platform_map = get_platform_map()
    # 从 .ysm 收集所有 model_blocks（去重）
    seen: set[str] = set()
    raw_blocks: list[dict] = []
    for f in ysm_files:
        meta = lib_ysm.extract_metadata(f, quiet=True)
        blocks = meta.get('author_blocks') or []
        if not blocks:
            continue
        _, model_blocks, _ = lib_ysm.classify_authors(blocks)
        for b in model_blocks:
            name = (b.get('name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            raw_blocks.append(b)
    if not raw_blocks:
        # 无 .ysm 或无作者块：回退到 primary_entry
        return [{'name': primary_entry.get('name', []),
                 'role': primary_entry.get('role', ''),
                 'platforms': primary_entry.get('platforms', {}),
                 'author_id': primary_id}]

    # 主作者 = 第一个块，合并 primary_entry 的平台数据
    primary_name = (raw_blocks[0].get('name') or '').strip()
    result: list[dict] = []
    for i, b in enumerate(raw_blocks):
        name = (b.get('name') or '').strip()
        if i == 0:
            # 主作者：合并 authors.json 数据
            platforms = primary_entry.get('platforms') or {}
            if not platforms and b.get('contacts'):
                platforms = lib_ysm.map_platforms(b.get('contacts') or {}, platform_map)
            role = primary_entry.get('role') or b.get('role', '')
            result.append({
                'name': primary_entry.get('name') or [name],
                'role': role,
                'platforms': platforms,
                'author_id': primary_id,
            })
        else:
            # 其他模型作者：从 .ysm 原始块提取，name 加 # 前缀（与作者级 README 保持一致）
            clean_name, url_platforms = _clean_name_with_urls(name, platform_map)
            platforms = lib_ysm.map_platforms(b.get('contacts') or {}, platform_map)
            if url_platforms:
                for field, lines in url_platforms.items():
                    platforms.setdefault(field, []).extend(lines)
            result.append({
                'name': [f'#{clean_name}'],
                'role': b.get('role', ''),
                'platforms': lib_ysm.map_platforms(b.get('contacts') or {}, platform_map),
                'author_id': '',
            })
    return result


# 正则：匹配 URL（http/https）
_URL_RE = re.compile(r'https?://[^\s）\)\]>、，,，。]+')


def _clean_name_with_urls(name: str, platform_map: dict) -> tuple[str, dict]:
    """从含 URL 的 name 中提取链接到 platforms，返回 (清理后name, platforms)。

    处理格式如：
      "◆SIG556模型：伊洛是哥斯拉吗（https://space.bilibili.com/17798027）"
      "◆调试、Bug修复：GDHJDSYDH（https://...）、狱际星芒（https://...）"
    提取 URL 后归入 OtherPlatform，name 保留作者名部分。
    """
    cleaned = name.strip().lstrip('◆＃#')
    platforms: dict[str, list[str]] = {}
    urls = _URL_RE.findall(cleaned)
    if not urls:
        return name, platforms
    # 从 name 中移除所有 URL 及周围括号、顿号分隔符
    for url in urls:
        cleaned = cleaned.replace(url, '')
    cleaned = re.sub(r'[（(][\s]*[）)]?', '', cleaned)
    cleaned = re.sub(r'[）)]', '', cleaned)
    cleaned = re.sub(r'[、，,]+\s*$', '', cleaned)
    cleaned = cleaned.strip().strip('：:').strip()
    # 如果清理后 name 包含 ：（如 "SIG556模型：伊洛是哥斯拉吗"），取冒号后部分为作者名
    for sep in ('：', ':'):
        if sep in cleaned:
            parts = [p.strip() for p in cleaned.split(sep, 1)]
            if len(parts) == 2 and parts[1]:
                cleaned = parts[1]
                break
    if not cleaned:
        cleaned = name.strip().lstrip('◆＃#')
    # 将 URL 放入 OtherPlatform
    lines: list[str] = []
    for url in urls:
        domain = url.split('//')[-1].split('/')[0].lower() if '//' in url else ''
        if 'bilibili' in domain:
            lines.append(f'Bilibili: {url}')
        else:
            lines.append(f'Website: {url}')
    if lines:
        platforms['OtherPlatform'] = lines
    return cleaned, platforms


def get_co_creators(model_dir: Path) -> list[dict]:
    """按 "<作者编号>/<文件夹名>" 精确匹配 co_creators；文件夹被 rename_model_folders 改名时
    用 same_model 容错匹配（Unknown_ 前缀、规范化命名等变形）。

    co_creators 无记录（旧归档/手动放置的模型）时回退解析模型目录下 .ysm 的作者块，
    识别 co-creator —— .ysm 是作者信息的源头，覆盖 co_creators 未收录的情况。

    返回前自动清理 name 中的 URL（提取到 platforms）。
    """
    author_id = model_dir.parent.name
    meta = load_co_creators()
    exact = meta.get(f'{author_id}/{model_dir.name}')
    if exact is not None:
        return _clean_co_creators(exact.get('co_creators', []))
    for key, entry in meta.items():
        kid, _, kfolder = key.partition('/')
        if kid == author_id and same_model(kfolder, model_dir.name):
            return _clean_co_creators(entry.get('co_creators', []))
    return co_creators_from_ysm(model_dir)


def _clean_co_creators(co_creators: list[dict]) -> list[dict]:
    """清理 co-creator 列表中的 name 字段：提取 URL 到 platforms。"""
    platform_map = get_platform_map()
    result: list[dict] = []
    for c in co_creators:
        name = (c.get('name') or '').strip()
        if not name:
            continue
        clean_name, url_platforms = _clean_name_with_urls(name, platform_map)
        platforms = dict(c.get('platforms') or {})
        if url_platforms:
            for field, lines in url_platforms.items():
                platforms.setdefault(field, []).extend(lines)
        result.append({'name': clean_name, 'role': c.get('role', ''),
                       'platforms': platforms})
    return result


def co_creators_from_ysm(model_dir: Path) -> list[dict]:
    """解析模型目录下全部 .ysm，把非主作者块合并成 co-creator 记录（co_creators 兜底）。

    主作者 = 制作者信号最强的块（与归档分类 classify_authors 一致，见 lib/ysm.py）；其余
    块即 co-creator。多 .ysm 目录（同一模型的多个版本/变体）会**扫描全部文件并去重合并**，
    避免只取第一个文件而漏掉其他版本的合作作者。返回格式与 co_creators 的
    co_creators 相同：[{'name', 'role', 'platforms': {字段: [值]}}]。

    自动清理：name 含 URL 时提取到 platforms，保留干净的作者名。
    """
    ysm_files = sorted(model_dir.glob('*.ysm')) + sorted(model_dir.glob('*.YSM'))
    platform_map = get_platform_map()
    merged: list[dict] = []
    seen: set[str] = set()
    for f in ysm_files:
        meta = lib_ysm.extract_metadata(f, quiet=True)
        blocks = meta.get('author_blocks') or []
        if len(blocks) < 2:
            continue
        _, _, co_blocks = lib_ysm.classify_authors(blocks)
        for b in co_blocks:
            name = (b.get('name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            # 从 name 中提取 URL 到 platforms
            clean_name, url_platforms = _clean_name_with_urls(name, platform_map)
            platforms = lib_ysm.map_platforms(b.get('contacts') or {}, platform_map)
            if url_platforms:
                # 合并 URL 提取的 platforms
                for field, lines in url_platforms.items():
                    platforms.setdefault(field, []).extend(lines)
            merged.append({'name': clean_name, 'role': b.get('role', ''),
                           'platforms': platforms})
    return merged


# ---------------------------------------------------------------------------
# 渲染（按 templates/model_readme.template.json 的 author_block 格式）
# ---------------------------------------------------------------------------
def normalize_platforms(platforms: dict,
                        platform_map: dict) -> dict[str, list[tuple[str, str]]]:
    """把平台数据统一为 {分类: [(平台键, 值)]}，供 render_platform_block 渲染。

    兼容两种输入结构：
      - 扁平 {平台键: 值}（authors.json 的 platforms）→ 反查 platform_map 分类
      - 已分类 {分类: [值行]}（co_creators / ysm 解析的 co-creator platforms）
    """
    out: dict[str, list[tuple[str, str]]] = {}
    if not platforms:
        return out
    sample = next(iter(platforms.values()))
    if isinstance(sample, list):
        # 已分类：值行为 'Bilibili: https://...' 形式
        for field, lines in platforms.items():
            for line in lines:
                key, _, value = str(line).partition(':')
                out.setdefault(field, []).append((key.strip(), value.strip()))
    else:
        # 扁平：反查 platform_map（{分类: {规范名: [别名...]}}）得到所属分类
        reverse: dict[str, str] = {}
        for field, platforms_map in platform_map.items():
            for canonical, aliases in platforms_map.items():
                for alias in [canonical, *aliases]:
                    reverse.setdefault(alias.lower(), field)
        for key, value in platforms.items():
            field = reverse.get(str(key).strip().lower(), 'OtherPlatform')
            out.setdefault(field, []).append((str(key).strip(), str(value)))
    return out


def render_platform_block(platforms: dict, tpl: dict, label: str) -> list[str]:
    """渲染平台字段块：分类行（`**SocialPlatform**: #Bilibili #YouTube`）+ 平台子行。

    label 为平台链接的显示文本（作者规范名）；非 URL 值（QQ 号等）走纯文本子行。
    """
    items = normalize_platforms(platforms, get_platform_map())
    lines: list[str] = []
    for field in tpl.get('platform_order', []):
        pairs = items.get(field) or []
        if not pairs:
            continue
        tags = ' #'.join(key for key, _ in pairs)
        lines.append(tpl['platform_header'].format(field=field, tags=tags))
        for key, value in pairs:
            if value.startswith('http'):
                lines.append(tpl['platform_item'].format(platform=key, label=label, url=value))
            else:
                lines.append(tpl['platform_plain_item'].format(platform=key, value=value))
    return lines


def render_person_block(entry: dict, author_id: str = '',
                        default_role: bool = False) -> list[str]:
    """渲染作者/co-creator 信息块：Name + Author ID + Role + 平台分类段。

    entry: {'name': 数组或字符串, 'role': str, 'platforms': {...}}（两种平台结构均可）。
    author_id: 非空时作为 Name 子项（缩进）输出，先于 Role/平台。
    default_role: 主作者为 True 时，Role 缺省回退到模板默认值（.ysm 无 role 也显示）。
    """
    tpl = load_template().get('author_block', {})
    names = entry.get('name') or []
    if isinstance(names, str):
        names = lib_readme.split_author_names(names)
    names = [str(n) for n in names if str(n)]
    name_str = ' | '.join(names) if names else '暂无'
    label = str(names[0]).lstrip('#＃') if names else name_str

    lines = [tpl.get('name_line', '- **Name**: {names}').format(names=name_str)]
    if author_id:
        # Author ID 紧跟 Name 子项（模板缩进），先于 Role/平台
        lines.append(tpl.get('id_line', '  - **Author ID**: `{author_id}`')
                     .format(author_id=author_id))
    role = entry.get('role') or ''
    if role:
        # Role 值经术语表归一化：把 .ysm 的不同表达（Model author/动画/動作）
        # 统一为标准中英术语；已是标签格式（含 #/|）的原样保留。
        role = lib_terms.normalize_role(role)
    elif default_role:
        role = tpl.get('role_default', '')
    if role:
        lines.append(tpl.get('role_line', '  - **Role**: {role}').format(role=role))
    lines.extend(render_platform_block(entry.get('platforms') or {}, tpl, label))
    return lines


def build_co_creator_section(co_creators: list[dict]) -> str:
    """Co-creator Details 内容（多个人块，空行分隔）；无记录返回空串。"""
    if not co_creators:
        return ''
    lines: list[str] = []
    for c in co_creators:
        lines.extend(render_person_block(c))
        lines.append('')
    return '\n'.join(lines).rstrip()


def _human_size(n: int) -> str:
    """字节数人性化显示：<1KB 用 B、<1MB 用 KB，其余用 MB（均一位小数）。"""
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / (1024 * 1024):.1f} MB'


def build_download_lines(model_dir: Path, base_url: str) -> list[str]:
    """生成 Download 区块内容：每个可下载文件一行 `- [文件名 (大小)](raw 链接)`。

    收录范围 = 模型目录顶层、后缀在 DOWNLOAD_EXTS 白名单内的文件（.ysm 本体或
    zip/rar/7z 等压缩包，大小写不敏感；tar.gz 等复合后缀按最后一段匹配 .gz）。
    白名单而非 glob：Windows/Linux 大小写行为一致，且天然排除 README/预览图等。
    base_url 形如 https://raw.githubusercontent.com/<owner>/<repo>/<branch>；
    相对路径按仓库根计算（as_posix 保证 Windows 下也是 / 分隔），中文/空格/
    间隔号等非 ASCII 字符 percent-encoding 后拼接，避免破坏 Markdown 链接语法。
    无可下载文件时返回空列表（由调用方决定不输出该区块）。
    """
    files = sorted((f for f in model_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in DOWNLOAD_EXTS),
                   key=lambda p: p.name.lower())
    if not files:
        return []
    lines: list[str] = []
    for f in files:
        rel = f.relative_to(WORKSPACE_ROOT).as_posix()
        url = f'{base_url}/{quote(rel, safe="/")}'
        lines.append(f'- [{f.name} ({_human_size(f.stat().st_size)})]({url})')
    return lines


def build_meta_and_preview_content(model_dir: Path, image_paths: list[Path],
                                   category_tag: str, game_tags: str,
                                   co_creators: list[dict],
                                   model_authors: list[dict],
                                   role_name: str = '') -> str:
    """按模板渲染模型 README：Model Info（预览图 + Name/Category/Game 独立
    details）+ Download（独立 details）+ Author Info（大 details，内含
    Author/Co-creator 二级标题，details 循环后统一关闭）。所有 details 默认展开
    （模板 open=true 或 `<details open>`），顺序由模板 sections 决定。

    role_name: 模型文件夹名解析出的 `#中文 | #英文` 角色标签（Name 字段，可为空）。
    """
    tpl = load_template()
    title = model_dir.name
    lines = [tpl.get('title', '# {model_name}').format(model_name=title), '']
    opened_author_details = False

    for section in tpl.get('sections', []):
        key = section.get('key')
        if key == 'model_info':
            # 独立 details（默认展开）：预览图在前，Name/Category/Game 在后
            lines += [section['heading'], '<details open>',
                      f"<summary>{section['summary']}</summary>", '',
                      START_MARKER, '']
            for image_path in image_paths:
                rel_path = image_path.relative_to(model_dir).as_posix()
                lines.append(f'![{image_path.name}]({rel_path})')
                lines.append('')
            lines += [END_MARKER, '']
            for field in section.get('fields', []):
                indent = '  ' * field.get('indent', 0)
                if field.get('key') == 'category':
                    lines.append(f"{indent}- {field['label']}: {category_tag}")
                elif field.get('key') == 'game':
                    lines.append(f"{indent}- {field['label']}: {game_tags}")
                elif field.get('key') == 'name':
                    lines.append(f"{indent}- {field['label']}: {role_name}")
            lines += ['</details>', '']
        elif key == 'author_details':
            lines += [section['heading'], '']
            for ma in model_authors:
                lines += render_person_block(
                    ma, author_id=ma.get('author_id', ''),
                    default_role=True)
                lines.append('')
            if not model_authors:
                lines += ['- **Name**: 暂无', '']
        elif key == 'co_creator_details':
            # 无 co-creator 时连标题也不输出（避免空 section 占位）
            co_section = build_co_creator_section(co_creators)
            if co_section:
                lines += [section['heading'], '', co_section, '']
        elif key == 'download':
            # 下载直链独立 details（默认展开）：无 .ysm/压缩包等可下载文件时整个区块不输出
            dl_lines = build_download_lines(model_dir, RAW_BASE)
            if dl_lines:
                lines += [section['heading'], '<details open>',
                          f"<summary>{section['summary']}</summary>", '']
                lines += dl_lines
                lines += ['', '</details>', '']
        elif key == 'author_info':
            # 大 details 块（默认展开）：只含 Author/Co-creator 二级标题，字段已上移 Model Info
            lines += [section['heading'], '<details open>',
                      f"<summary>{section['summary']}</summary>", '']
            opened_author_details = True

    if opened_author_details:
        # 关闭 Author Info 的大 details（Author/Co-creator 位于其内）
        lines += ['</details>', '']
    return '\n'.join(lines).rstrip() + '\n'


def is_author_dir_name(name: str) -> bool:
    """判断目录名是否为作者目录（'<编号>-<作者名>' 格式，如 '0001-02Bunny,蓝玫瑰'）。

    作者目录在仓库根，编号为前 4 位数字、第 5 位为 '-'。
    """
    return len(name) >= 5 and name[:4].isdigit() and name[4] == '-'


def is_author_dir(path: Path) -> bool:
    return path.is_dir() and is_author_dir_name(path.name)


def _collect_ysm_dirs(d: Path):
    """递归收集模型目录：含 .ysm 文件或 previews/ 子目录即视为模型目录。

    适配 Other-YSM-Models 的 <作品>/<模型> 两层（或更深）组织（与 lib/kb/cmds.py
    的 _collect_model_dirs 同规则）；作品层（无 .ysm）继续向下找，避免把 AK/、
    BA/ 等作品目录误当模型目录生成 README。
    """
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    has_ysm = any(e.is_file() and e.suffix.lower() == '.ysm' for e in entries)
    has_previews = any(e.is_dir() and e.name == 'previews' for e in entries)
    if has_ysm or has_previews:
        yield d
        return
    for e in entries:
        if e.is_dir() and e.name != 'previews' and not e.name.startswith('.'):
            yield from _collect_ysm_dirs(e)


def iter_model_dirs(root_dir: Path):
    if root_dir == WORKSPACE_ROOT:
        # 作者目录在仓库根，命名 '<编号>-<作者名[,别名]>'；其下子目录为模型
        for author_dir in sorted(root_dir.iterdir()):
            if not is_author_dir(author_dir):
                continue
            for model_dir in sorted(author_dir.iterdir()):
                if not model_dir.is_dir():
                    continue
                if model_dir.name.startswith('.') or model_dir.name.lower() == 'previews':
                    continue
                yield model_dir
    elif root_dir.name == 'Other-YSM-Models':
        # Other-YSM-Models 现按 <作品>/<模型> 两层（或更深）组织：递归收集模型目录
        for work_dir in sorted(root_dir.iterdir()):
            if work_dir.is_dir() and not work_dir.name.startswith('.'):
                yield from _collect_ysm_dirs(work_dir)
    else:
        # Blockbench-Models 等：保持一层遍历（子目录即模型目录）
        for model_dir in sorted(root_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            if model_dir.name.startswith('.') or model_dir.name.lower() == 'previews':
                continue
            yield model_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', metavar='PATH', default=None,
                        help='（已弃用，改用位置参数）只处理指定模型目录，如 Models/0056/xxx')
    parser.add_argument('--apply', action='store_true',
                        help='真正写入 README（默认 dry-run 仅预览）')
    parser.add_argument('paths', nargs='*', default=None,
                        help='指定路径处理（可多个，如 Models/0110 或 Models/0110/模型名；'
                             '不传则默认全量扫描）')
    args = parser.parse_args()

    apply_mode = args.apply
    mode_label = "执行" if apply_mode else "预览（dry-run，加 --apply 执行）"
    print(f"模式: {mode_label}")

    updated = 0
    created = 0
    skipped = 0
    unchanged = 0

    work_category_map = load_work_categories()
    works = load_works()

    # 收集模型目录：优先位置参数，其次 --only（兼容旧用法），最后全量
    if args.paths:
        model_dirs: list[Path] = []
        for p in args.paths:
            target = WORKSPACE_ROOT / p
            if not target.exists():
                # 尝试直接解析
                tp = Path(p).resolve()
                if tp.is_dir():
                    target = tp
                else:
                    print(f"[错误] 路径不存在: {p}", file=sys.stderr)
                    continue
            if is_author_dir(target):
                # 作者目录：收集其下所有模型子目录
                for sub in sorted(target.iterdir()):
                    if sub.is_dir() and not sub.name.startswith('.') and sub.name.lower() != 'previews':
                        if any(f.suffix.lower() == '.ysm' for f in sub.glob('*') if f.is_file()) or \
                           any(e.is_dir() and e.name == 'previews' for e in sub.iterdir()):
                            model_dirs.append(sub)
            elif target.is_dir():
                # 单个模型目录或其他目录
                if any(f.suffix.lower() == '.ysm' for f in target.glob('*') if f.is_file()) or \
                   any(e.is_dir() and e.name == 'previews' for e in target.iterdir()):
                    model_dirs.append(target)
                else:
                    # 可能是 Other-YSM-Models 的作品目录：递归收集
                    model_dirs.extend(_collect_ysm_dirs(target))
        if not model_dirs:
            print("[错误] 未找到任何模型目录", file=sys.stderr)
            return 2
    elif args.only:
        target = WORKSPACE_ROOT / args.only
        model_dirs = [target] if target.is_dir() else []
        if not model_dirs:
            print(f"[错误] 目录不存在: {target}", file=sys.stderr)
            return 2
    else:
        model_dirs = [md for root_dir in ROOT_DIRS if root_dir.is_dir()
                      for md in iter_model_dirs(root_dir)]

    roles, e2c, c2e = get_role_parser()
    for model_dir in model_dirs:
        # 全部模型目录都生成 README（不要求存在预览图）
        preview_images = collect_preview_images(model_dir)
        co_creators = get_co_creators(model_dir)
        author_id, author_entry = get_author_info(model_dir)
        if not author_entry.get('name'):
            author_entry = {'name': [], 'platforms': {}}
        # Author Role 以模型 .ysm 主作者 role 为准；无则渲染时用模板默认值
        if not author_entry.get('role'):
            author_entry['role'] = get_main_author_role(model_dir)
        # 获取所有模型作者（含非 primary），用于 Author 区渲染
        model_authors = get_model_authors(model_dir, author_entry, author_id)
        # 角色名（Model Details 的 Name 字段）：resolve_name3 解析模型文件夹名，
        # 显示为 `#中文 | #英文` 标签格式（如 #圣园未花 | #Mika-Misono）
        try:
            r = resolve_name3(model_dir.name, roles, e2c, c2e)
            role_name = ' | '.join(f'#{x}' for x in (r.get('zh') or '', r.get('en') or '')
                                   if x)
        except Exception:  # noqa: BLE001
            role_name = ''
        category_tag = get_category_tag(model_dir.name, work_category_map)
        game_tags = get_work_tags(works, model_dir.name.split('_')[0])

        readme_path = model_dir / 'README.md'
        existing_content = readme_path.read_text(
            encoding='utf-8', errors='ignore') if readme_path.exists() else None

        new_content = build_meta_and_preview_content(
            model_dir, preview_images, category_tag, game_tags,
            co_creators, model_authors, role_name)

        if readme_path.exists():
            if existing_content == new_content:
                unchanged += 1
                continue
            action = 'Updated'
            updated += 1
        else:
            action = 'Created'
            created += 1

        rel = readme_path.relative_to(WORKSPACE_ROOT)
        if apply_mode:
            readme_path.write_text(new_content, encoding='utf-8')
            print(f"{action} {rel}")
        else:
            print(f"[计划] {action} {rel}")

    print(f"Summary: created={created}, updated={updated}, unchanged={unchanged}（{mode_label}）")

    # 回退解析发现的作者合并进 authors.json（--apply 才写盘）
    if _NEW_AUTHORS:
        path = lib_paths.data_path('author-info', 'authors.json')
        data = lib_paths.load_json(path, {})
        authors = data.setdefault('authors', {})
        added = 0
        for aid, info in sorted(_NEW_AUTHORS.items()):
            if aid in authors:
                continue  # 已在 authors.json 中（本次循环里新加入的除外）
            authors[aid] = {
                'name': info.get('name') or [aid],
                'platforms': info.get('platforms') or {},
            }
            added += 1
            print(f'{"[已添加]" if apply_mode else "[将添加]"} authors.json: {aid} '
                  f'{"、".join(authors[aid]["name"])}')
        if apply_mode and added:
            lib_paths.save_json(path, data)
            print(f'已把 {added} 位回退解析作者合并进 authors.json')
        elif added and not apply_mode:
            print(f'预览：{added} 位回退解析作者待合并进 authors.json（加 --apply 写入）')

    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
