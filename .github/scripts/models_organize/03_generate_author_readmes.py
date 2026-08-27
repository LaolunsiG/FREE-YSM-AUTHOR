#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 作者 README 生成工具——按 authors.json 数据生成作者 README。

作者 README 直接由集中作者数据（author-info/authors.json）渲染：
  # <编号> + ## Author + Name + 平台分类段（无 Role）。
作者在不同模型里负责的功能不一致，作者级 Role 已废弃，角色只记录在
模型级（co_creators.json / .ysm 作者块）。

作者目录在仓库根，命名 '<编号>-<作者名[,别名]>'（如 '0001-02Bunny,蓝玫瑰'），
脚本按编号前缀（'0001-'）匹配目录；README 写在对应作者目录下。

用法:
  python .github/scripts/models_organize/03_generate_author_readmes.py                       # 合并模式（默认：先反合 README 手写信息进 authors.json，再生成 README）预览（dry-run）
  python .github/scripts/models_organize/03_generate_author_readmes.py --apply               # 合并模式并写入
  python .github/scripts/models_organize/03_generate_author_readmes.py --no-merge --apply    # 覆盖模式：从 authors.json 直接生成 README（不反合）
  python .github/scripts/models_organize/03_generate_author_readmes.py --overwrite-author --apply  # 反向覆盖：README -> authors.json（以 README 为权威）
  python .github/scripts/models_organize/03_generate_author_readmes.py 0058 0093             # 指定编号（合并模式 dry-run）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths
from lib import readme as lib_readme
from lib.author_readme import render_author_readme, load_tag_labels
from lib.kb.authors import merge_author_updates

REPO_ROOT = lib_paths.WORKSPACE_ROOT


def is_author_dir_name(name: str) -> bool:
    """判断目录名是否为作者目录：'<编号>-<作者名[,别名]>' 或裸 '<编号>'（未收录新作者）。"""
    return (len(name) >= 4 and name[:4].isdigit()
            and (len(name) == 4 or name[4] == '-'))


def is_plain_author_dir_name(name: str) -> bool:
    """是否为裸编号作者目录（'<编号>'，尚无作者名后缀，如自动收录前的新目录）。"""
    return len(name) == 4 and name.isdigit()


def author_dir_for(root: Path, aid: str) -> Path | None:
    """按编号查找作者目录：优先 '<编号>-…' 前缀，其次裸 '<编号>'；不存在返回 None。"""
    prefix = f'{aid}-'
    dirs = sorted(root.iterdir())
    for d in dirs:
        if d.is_dir() and d.name.startswith(prefix):
            return d
    for d in dirs:
        if d.is_dir() and d.name == aid:
            return d
    return None


def iter_author_dirs(root: Path):
    """遍历仓库根下所有作者目录（'<编号>-…' 格式），按目录名排序。"""
    for d in sorted(root.iterdir()):
        if d.is_dir() and is_author_dir_name(d.name):
            yield d


def author_entries(root: Path,
                   only: list[str] | None = None) -> list[tuple[str, dict]]:
    """返回 (编号, entry) 列表：authors.json 里有 name 且目录存在的作者；only 限定编号。"""
    data = lib_readme.load_authors_index()
    authors = data.get('authors') or {}
    out: list[tuple[str, dict]] = []
    for aid in sorted(authors):
        if only and aid not in only:
            continue
        entry = authors[aid]
        if not (entry.get('name') or []):
            continue  # 无名字的作者跳过
        if author_dir_for(root, aid) is None:
            continue  # 目录不存在的作者（幽灵条目）跳过
        out.append((aid, entry))
    return out


# ---------------------------------------------------------------------------
# 合并模式：把作者 README 的手写信息（team/平台）反向合并进 authors.json
# ---------------------------------------------------------------------------
TEAM_LINE_RE = re.compile(r'^\s*-\s*\*\*team\*\*\s*[:：]\s*(?P<val>.+)$',
                          re.MULTILINE | re.IGNORECASE)
NAME_LINE_RE = re.compile(r'^\s*-\s*\*\*Name\*\*\s*[:：]\s*(?P<val>.+)$',
                          re.MULTILINE | re.IGNORECASE)
PLATFORM_SUB_RE = re.compile(r'^\s{2,}-\s*\*\*(?P<key>[^*]+)\*\*\s*[:：]\s*(?P<val>.*)$')
BADGES_LINE_RE = re.compile(r'^\s*-\s*\*\*(?:badges|徽章)\*\*\s*[:：]\s*(?P<val>.+)$',
                          re.MULTILINE | re.IGNORECASE)


def _badge_text_to_keys(labels: dict, text: str) -> list[str]:
    """把作者 README badges 行的显示文本反查为词表键（推荐/Recommended -> recommended）。

    badges 行格式：`#High-Output | #高产 #推荐`（空格与 | 分隔、标签带 # 前缀）。
    支持分隔符 空格 |｜ ·•、,，;；；反查匹配中文名/英文名/中英成对。
    """
    keys: list[str] = []
    for seg in re.split(r'[\s|｜·•、,，;；]+', text):
        seg = seg.strip().lstrip('#＃').strip()
        if not seg:
            continue
        for k, meta in labels.items():
            zh = str(meta.get('zh') or '')
            en = str(meta.get('en') or '')
            if seg in (zh, en, f'{zh}/{en}'):
                if k not in keys:
                    keys.append(k)
                break
    return keys


def parse_readme_author_info(text: str) -> dict:
    """从作者 README 提取可反向合并的作者信息：{aliases, team, badges, platforms}。

    aliases 取 `- **Name**: #名1 | #名2` 行（去 # 前缀、按 | 分割），供合并模式
    追加去重 / 覆盖模式全量覆盖 names。team 取 `- **team**: <值>` 行（忽略大小写）；
    平台取缩进子行 `    - **Key**: [label](url)` / `    - **Key**: 值`
    （[label](url) 还原为 url）。无信息返回 {}。
    """
    info: dict = {}
    m = NAME_LINE_RE.search(text)
    if m and m.group('val').strip():
        # Name 行格式：#伊洛是哥斯拉嘛 | #伊洛是哥斯拉吗 | ...（去 # 前缀、按 | 分割）
        names = [seg.strip().lstrip('#＃').strip()
                 for seg in re.split(r'[|｜]+', m.group('val'))
                 if seg.strip()]
        if names:
            info['aliases'] = names
    m = TEAM_LINE_RE.search(text)
    if m and m.group('val').strip():
        info['team'] = m.group('val').strip()
    m = BADGES_LINE_RE.search(text)
    if m:
        badges = _badge_text_to_keys(load_tag_labels(), m.group('val'))
        if badges:
            info['badges'] = badges
    platforms: dict[str, str] = {}
    for line in text.splitlines():
        m = PLATFORM_SUB_RE.match(line)
        if not m:
            continue
        key = m.group('key').strip()
        val = m.group('val').strip()
        if not val:
            continue
        um = re.match(r'^\[[^\]]*\]\((?P<url>https?://[^)]+)\)$', val)
        if um:
            val = um.group('url')
        platforms[key] = val
    if platforms:
        info['platforms'] = platforms
    return info


def merge_readmes_to_authors(root: Path,
                             entries: list[tuple[str, dict]],
                             apply: bool,
                             overwrite: bool = False) -> int:
    """合并模式：把作者 README 手写的 team/平台信息反向合并进 authors.json。

    解析每个作者 README → merge_author_updates 按编号/别名匹配合并
    （幂等：平台只补缺失、team 非空写）→ --apply 写回 authors.json。
    overwrite=True 时改为覆盖模式（以 README 为权威覆盖/新增字段，保留未出现字段）。
    返回合并的作者数。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.get('authors') if isinstance(data, dict) else None
    if not authors:
        print('authors.json 缺失或为空，跳过合并。')
        return 0
    # 用 entries（含自动标签，内存态）同步回 authors，避免重新读盘丢失自动标签
    for aid, entry in entries:
        if aid in authors:
            authors[aid] = entry
    updates: dict[str, dict] = {}
    for aid, _entry in entries:
        author_dir = author_dir_for(root, aid)
        readme = (author_dir / 'README.md') if author_dir else None
        info = {}
        if readme and readme.is_file():
            info = parse_readme_author_info(
                readme.read_text(encoding='utf-8', errors='ignore'))
        # 自动判定徽章（high-output/nsfw）合并进 badges（无论 README 有无 badges 行）
        auto = auto_author_marks(_author_model_count(author_dir), author_dir)
        if auto:
            cur = {str(b).lower() for b in (info.get('badges') or [])}
            merged = list(dict.fromkeys(
                (info.get('badges') or []) + [b for b in auto if b not in cur]))
            info['badges'] = merged
        if info:
            updates[aid] = info
    matched, unmatched = merge_author_updates(authors, updates, overwrite=overwrite)
    mode_label = '覆盖' if overwrite else '合并'
    for aid, changes in matched:
        print(f'  [{mode_label}] {aid}  {"、".join(changes)}')
    for key in unmatched:
        print(f'  [未匹配] {key}（authors.json 无此作者，未{mode_label}）')
    if matched and apply:
        lib_paths.save_json(path, data)
        print(f'已{mode_label} {len(matched)} 位作者的 README 信息 -> authors.json')
    elif matched:
        print(f'{mode_label}模式: 共 {len(matched)} 位作者待{mode_label}（加 --apply 写入 authors.json）')
    print(f'扫描 {len(entries)} 位作者，{len(matched)} 位有变更')
    return len(matched)


# ---------------------------------------------------------------------------
# 自动判定标签（仅生成作者 README 时追加显示，不进 authors.json）
# ---------------------------------------------------------------------------
HIGH_OUTPUT_THRESHOLD = 20          # 模型数 ≥ 此值 → 高产 标签
R18_KEYWORDS = ('nsfw', 'r18', 'r-18', '18+')   # 模型文件夹名含 → 18禁 标签


def auto_author_marks(model_count: int, author_dir: Path | None) -> list[str]:
    """自动判定的标签键列表（作者 README 的 **badges**: 追加显示，不写 authors.json）。

    high-output: 模型数 ≥ 阈值；nsfw: 目录下模型文件夹名含 nsfw/r18/18+。
    根 README 不用本函数（其标记完全由 authors.json 的 badges 驱动）。
    """
    marks: list[str] = []
    if model_count >= HIGH_OUTPUT_THRESHOLD:
        marks.append('high-output')
    pat = re.compile('|'.join(re.escape(k) for k in R18_KEYWORDS), re.IGNORECASE)
    if author_dir and author_dir.is_dir() and any(pat.search(p.name) for p in author_dir.iterdir()
                                                  if p.is_dir() and not p.name.startswith('.')):
        marks.append('nsfw')
    return marks


def _author_model_count(author_dir: Path | None) -> int:
    """作者目录下模型文件夹数（排除 previews 与隐藏目录）。"""
    if not author_dir or not author_dir.is_dir():
        return 0
    return sum(1 for p in author_dir.iterdir()
               if p.is_dir() and not p.name.startswith('.')
               and p.name.lower() != 'previews')


def _merge_auto_badges(entries: list[tuple[str, dict]],
                       root: Path) -> bool:
    """把自动判定徽章（high-output/nsfw）追加合并进每个 entry.badges，返回是否有变更。

    供“读取来源 badges 后、执行合并/覆盖前”调用：无论来源是 author.json 还是 README，
    自动判定徽章都被合并进既有 badges（追加去重），确保三种模式都保留自动徽章。
    """
    changed = False
    for aid, entry in entries:
        author_dir = author_dir_for(root, aid)
        auto = auto_author_marks(_author_model_count(author_dir), author_dir)
        if not auto:
            continue
        cur = {str(b).lower() for b in (entry.get('badges') or [])}
        for b in auto:
            if b not in cur:
                entry.setdefault('badges', []).append(b)
                cur.add(b)
                changed = True
    return changed


def prune_ghost_authors(root: Path, apply: bool) -> int:
    """删除幽灵作者：authors.json 中无对应作者文件夹的条目（--apply 写盘）。

    幽灵作者是历史遗留（作者目录已合并/删除/重编号），留着会造成 authors 数与
    目录不一致。dry-run 逐个预览，加 --apply 才删除并写回 authors.json。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.get('authors') if isinstance(data, dict) else None
    if not authors:
        print('authors.json 缺失或为空，跳过。')
        return 0
    ghosts = sorted(aid for aid in authors if author_dir_for(root, aid) is None)
    if not ghosts:
        print('无幽灵作者（所有作者均有对应文件夹）。')
        return 0
    print(f'幽灵作者（无文件夹）共 {len(ghosts)} 位：')
    for aid in ghosts:
        names = '、'.join(authors[aid].get('name') or [])
        print(f'  {"[删除]" if apply else "[计划]"} {aid}  {names}')
    if apply:
        for aid in ghosts:
            del authors[aid]
        lib_paths.save_json(path, data)
        print(f'已删除 {len(ghosts)} 位幽灵作者并写回 authors.json。')
    else:
        print('(dry-run) 加 --apply 执行删除。')
    return 0


def auto_add_missing_authors(root: Path, apply: bool) -> int:
    """自动收录作者：README 含作者信息、authors.json 无此编号时按顺序新增条目。

    扫描根目录所有作者形态目录（'<编号>-<名称>' 或裸 '<编号>'），对 authors.json
    中缺失的编号，从该目录 README 解析作者信息（Name 别名 / platforms / team /
    badges，复用 parse_readme_author_info）并新增条目——编号沿用目录编号（保持
    顺序），不重新排序。dry-run 预览，--apply 写盘。

    用于合并/覆盖模式：新模型目录（如 0207/）README 写有作者名但 authors.json
    尚无记录时，自动补上，避免作者丢失。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.setdefault('authors', {}) if isinstance(data, dict) else None
    if authors is None:
        print('authors.json 缺失或为空，跳过自动收录。')
        return 0

    dirs = sorted((d for d in root.iterdir()
                   if d.is_dir() and is_author_dir_name(d.name)), key=lambda d: d.name)
    added = 0
    for d in dirs:
        aid = d.name[:4]
        if aid in authors:
            continue
        readme = d / 'README.md'
        if not readme.is_file():
            continue
        info = parse_readme_author_info(
            readme.read_text(encoding='utf-8', errors='ignore'))
        names = info.get('aliases') or []
        if not names:
            continue  # README 无作者名，无法收录
        entry = {'name': names}
        if info.get('platforms'):
            entry['platforms'] = info['platforms']
        if info.get('badges'):
            entry['badges'] = info['badges']
        if info.get('team'):
            entry['team'] = info['team']
        print(f'  {"[已添加]" if apply else "[将添加]"} {aid}  {"、".join(names)}'
              f'（来自 {d.name}/README.md）')
        if apply:
            authors[aid] = entry
        added += 1

    if apply and added:
        lib_paths.save_json(path, data)
        print(f'已自动收录 {added} 位作者 -> authors.json')
    elif added:
        print(f'预览：{added} 位作者待自动收录（加 --apply 写入 authors.json）')
    else:
        print('无 README 含作者信息但 authors.json 缺失的目录。')
    return added


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('authors', nargs='*',
                        help='作者编号（可多个；不给则按 authors.json 全量生成）')
    parser.add_argument('--root', metavar='PATH', default=None,
                        help='仓库根目录（默认自动检测）')
    parser.add_argument('--apply', action='store_true',
                        help='真正写入（默认 dry-run 只预览）')
    parser.add_argument('--overwrite-readme', action='store_true',
                        help='覆盖生成：从 authors.json 生成作者 README（--no-merge 的别名，可省略）')
    parser.add_argument('--overwrite-author', action='store_true',
                        help='反向覆盖：从作者 README 覆盖到 authors.json（加 --apply 写盘，不生成 README）')
    parser.add_argument('--merge', action=argparse.BooleanOptionalAction, default=True,
                        help='合并模式：先反向合并 README 手写信息进 authors.json，再生成 README'
                             '（默认启用；使用 --no-merge 切换为覆盖模式）')
    parser.add_argument('--prune-ghosts', action='store_true',
                        help='删除幽灵作者：authors.json 中无对应作者文件夹的条目（dry-run 预览，加 --apply 写盘）')
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else REPO_ROOT
    if not root.is_dir():
        print(f'错误: {root} 目录不存在。')
        return 2

    # --prune-ghosts：删除无文件夹的幽灵作者条目
    if args.prune_ghosts:
        return prune_ghost_authors(root, args.apply)

    # 自动收录：README 含作者信息、authors.json 缺失的作者，按编号顺序补条目
    #（合并/覆盖模式共用；--apply 才写盘，随后生成的 README 才包含新作者）
    auto_add_missing_authors(root, args.apply)

    only = [a.zfill(4) for a in args.authors] if args.authors else None
    entries = author_entries(root, only)

    # --overwrite-author：反向覆盖 README -> authors.json，不生成 README
    if args.overwrite_author:
        merged = merge_readmes_to_authors(root, entries, args.apply, overwrite=True)
        return 0 if merged >= 0 else 1
    if not entries:
        print('authors.json 中没有可生成的作者。')
        return 0

    # 默认 / --overwrite-readme = 覆盖生成（JSON -> README）；--merge = 合并模式
    mode = '合并' if args.merge else '覆盖'
    print(f'将按 authors.json 处理 {len(entries)} 位作者的 README（{mode}模式）：')
    badges_updated = False
    if args.merge:
        # 合并模式：读取 README badges 后（内部合自动判定）反向合并进 authors.json
        merge_readmes_to_authors(root, entries, args.apply)
        # 重新读取合并后的 authors.json，渲染使用最新 team/badges
        entries = author_entries(root, only)
    else:
        # 覆盖生成：读取 author.json 的 badges 后，合并自动判定徽章（追加去重），再渲染 README
        badges_updated = _merge_auto_badges(entries, root)

    generated = 0
    for aid, entry in entries:
        names = ' | '.join(entry.get('name') or [])
        print(f"  {'[生成]' if args.apply else '[计划]'} {aid}  {names}")
        if args.apply:
            author_dir = author_dir_for(root, aid)
            if author_dir is None:
                print(f'  [跳过] {aid} 未找到作者目录')
                continue
            models = sorted(p.name for p in author_dir.iterdir()
                            if p.is_dir() and not p.name.startswith('.')
                            and p.name.lower() != 'previews')
            readme = author_dir / 'README.md'
            readme.write_text(render_author_readme(aid, entry, models, author_dir),
                              encoding='utf-8')
            generated += 1

    if args.apply and badges_updated:
        path = lib_paths.data_path('author-info', 'authors.json')
        data = lib_paths.load_json(path, {})
        for aid, entry in entries:
            data.setdefault('authors', {})[aid] = entry
        lib_paths.save_json(path, data)
        print(f'已把自动判定标签写入 authors.json：{path}')

    if args.apply:
        print(f'已生成 {generated} 个作者 README。')
    else:
        print('dry-run 预览：未写入。加 --apply 执行。')
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())