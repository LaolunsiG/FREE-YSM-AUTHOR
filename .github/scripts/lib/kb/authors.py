# -*- coding: utf-8 -*-
"""作者维护命令：合并重复作者 / 从模型 .ysm 推导作者名 / 重建作者数据。

与 cmds.py（角色/作品，model-info/）平行，本模块承载"作者"维度
（author-info/authors.json、co_creators.json、Models/<编号>/README.md）的维护逻辑。
分层：text -> parse -> storage / sync -> cmds；本模块与 cmds 同级，互不依赖。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib import console as lib_console  # noqa: E402
from lib import models as lib_models  # noqa: E402
from lib import paths as lib_paths  # noqa: E402
from lib import readme as lib_readme  # noqa: E402
from lib import ysm as lib_ysm  # noqa: E402

REPO_ROOT = lib_paths.WORKSPACE_ROOT
MODELS_DIR = REPO_ROOT / 'Models'


# ---------------------------------------------------------------------------
# 从模型 .ysm 推导作者名
# ---------------------------------------------------------------------------
# 作者块是作者信息的可靠来源（README 可能缺失/错误），但 authors 字段常含
# URL、组合描述等噪音——推导名经 _is_usable_derived_name 过滤。
_DERIVED_NOISE_RE = re.compile(
    r'https?://|&amp;|&|＆|——|来自|来源|配置|素体|完整版|表情|精修|模型作者|动作作者|（模型）|（配置）|（动作）'
    r'|b站|B站|bb站[:：]|如有|定制|联系|qq[:：]')


def _is_usable_derived_name(name: str, existing_norms: set[str]) -> bool:
    """推导名可用性：无 URL/组合描述噪音、长度达标，且与现有 name 不重复
    （规范化相等或互为子串，避免 '02Bunny（蓝玫瑰）' 撞已有 '#02Bunny'）。"""
    norm = lib_readme.normalize_alias(name)
    if not norm or len(norm) < 2:
        return False
    if _DERIVED_NOISE_RE.search(name):
        return False
    return not any(norm == e or norm in e or e in norm for e in existing_norms)


def sync_authors_from_models(apply: bool = False) -> int:
    """从各作者目录下模型 .ysm 推导主作者名，作为补充别名并入 authors.json。

    默认 dry-run 只报告；--apply 才写回 authors.json。返回更新的作者数。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.get('authors') if isinstance(data, dict) else None
    if not authors:
        print('authors.json 缺失或为空，先运行 cli.py authors 生成')
        return 0
    updated = 0
    for author_dir in sorted(MODELS_DIR.iterdir()):
        if not (author_dir.is_dir() and re.fullmatch(r'\d{4}', author_dir.name)):
            continue
        derived: list[str] = []
        for model_dir in sorted(author_dir.iterdir()):
            if not (model_dir.is_dir() and not model_dir.name.startswith('.')
                    and model_dir.name.lower() != 'previews'):
                continue
            owner, _ = lib_ysm.model_owner(model_dir)
            if owner:
                derived.append('#' + owner.lstrip('#＃'))
        if not derived:
            continue
        aid = author_dir.name
        entry = authors.get(aid)
        names = list(entry.get('name') or []) if entry else []
        existing_norms = {lib_readme.normalize_alias(n) for n in names if n}
        usable: list[str] = []
        seen: set[str] = set()
        for d in derived:
            norm = lib_readme.normalize_alias(d)
            if norm in seen or not _is_usable_derived_name(d, existing_norms):
                continue
            seen.add(norm)
            usable.append(d)
        if not usable:
            continue
        print(f'  {aid}: {len(usable)} 个候选推导名 {usable}')
        if apply:
            if entry is None:
                entry = {'name': [], 'readme': f'Models/{aid}/README.md',
                         'platforms': {}}
                authors[aid] = entry
            entry['name'] = names + usable
            updated += 1
    if apply:
        if updated:
            lib_paths.save_json(path, data)
            print(f'已更新 {updated} 位作者的 name（模型 .ysm 推导，去重过滤）: {path}')
        else:
            print('无需更新：没有可用的模型推导名')
    else:
        print(f'dry-run: 共 {updated} 位作者有候选（加 --apply 写入 authors.json）')
    return updated


# ---------------------------------------------------------------------------
# 作者数据重建（authors.json）
# ---------------------------------------------------------------------------
def build_authors_data() -> dict:
    """扫描 Models 与根 README，构建集中作者数据（原 03_generate_root_readme --data）。"""
    return lib_readme.build_authors_data(MODELS_DIR, REPO_ROOT / 'README.md')


def write_authors_data(check_only: bool = False) -> int:
    """生成/检查 authors.json；check 只比较作者本体（generated 时间戳每次变）。

    重建时保留现有作者的额外键（tags/recommended 等手工标记），避免丢失。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = build_authors_data()
    authors = data['authors']
    # 合并保留现有额外键（非 name/readme/platforms 的字段，如 tags/recommended）
    existing = lib_paths.load_json(path, {})
    for aid, entry in authors.items():
        old = (existing.get('authors') or {}).get(aid)
        if old:
            for k, v in old.items():
                if k not in ('name', 'readme', 'platforms') and k not in entry:
                    entry[k] = v
    platform_count = sum(bool(a['platforms']) for a in authors.values())
    if check_only:
        existing = lib_paths.load_json(path, None) or {}
        changed = existing.get('authors') != authors
        print(f'{len(authors)} 位作者，平台字段 {platform_count} 个')
        if changed:
            print(f'Would write: {lib_paths.get_safe_relpath(path)}')
        else:
            print('No change needed')
        return 1 if changed else 0
    lib_paths.save_json(path, data)
    print(f'Written {len(authors)} authors -> {lib_paths.get_safe_relpath(path)}'
          f'（含平台信息 {platform_count} 位）')
    return 0


def add_author_alias() -> int:
    """交互式为指定作者添加别名：搜索作者 → 选择 → 输入别名 → 确认写入。

    别名追加到 authors.json 的 name 数组末尾（首项仍为规范名），
    按规范化别名去重，与现有名字重复的直接跳过。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.get('authors') if isinstance(data, dict) else None
    if not authors:
        print('authors.json 缺失或为空，先运行 author rebuild 生成')
        return 1

    while True:
        print('-' * 50)
        kw = lib_console.ask('搜索作者（编号或名字关键词，q=退出）: ').strip()
        if kw.lower() in ('q', 'quit'):
            break
        if not kw:
            continue
        hits = []
        for aid, entry in sorted(authors.items()):
            names = entry.get('name') or []
            if kw == aid or any(kw.lower() in n.lower() for n in names):
                hits.append((aid, entry))
        if not hits:
            print('未找到匹配作者。')
            continue
        print(f'命中 {len(hits)} 位：')
        for i, (aid, entry) in enumerate(hits, 1):
            names = ' / '.join(entry.get('name') or [])
            print(f"  [{i}] {aid}  {names}")
        sel = lib_console.ask('选择编号（Enter=重新搜索, q=退出）: ').strip()
        if sel.lower() in ('q', 'quit'):
            break
        if not sel.isdigit() or not (1 <= int(sel) <= len(hits)):
            print('编号无效。')
            continue
        aid, entry = hits[int(sel) - 1]
        names = list(entry.get('name') or [])
        print(f"  当前名字: {' / '.join(names)}（首项=规范名，其余=别名）")
        alias = lib_console.ask('  输入新别名（可多个用逗号分隔；Enter=取消）: ').strip()
        if not alias or alias.lower() in ('q', 'quit'):
            continue
        candidates = [a.strip() for a in alias.replace('，', ',').split(',') if a.strip()]
        # 与现有名字（规范化后）去重，避免重复别名
        existing_norms = {lib_readme.normalize_alias(n) for n in names if n}
        to_add: list[str] = []
        for a in candidates:
            norm = lib_readme.normalize_alias(a)
            if not norm:
                continue
            if norm in existing_norms:
                print(f"    跳过 {a!r}：与现有名字重复")
                continue
            existing_norms.add(norm)
            to_add.append(a)
        if not to_add:
            print('  没有可添加的新别名。')
            continue
        print(f"  将添加别名: {' / '.join(to_add)}")
        confirm = lib_console.ask('  确认写入？(y/n): ').strip().lower()
        if confirm not in ('y', 'yes'):
            print('  已取消。')
            continue
        entry['name'] = names + to_add
        lib_paths.save_json(path, data)
        print(f"  已为 {aid} 添加别名: {' / '.join(to_add)}")


# ---------------------------------------------------------------------------
# 合并重复作者（--merge-authors）
# 注意：本命令会移动/删除作者目录、改写作者 README——是 kb_tool.py 中
#       "只维护数据库、不改文件夹" 的唯一例外（其余命令仍不改文件夹/文件名）。
# ---------------------------------------------------------------------------
def normalize_platform_val(key: str, val: str) -> str:
    """平台值归一化（URL 去协议/尾部斜杠，便于对比）。"""
    v = val.strip().lower()
    v = re.sub(r'^https?://', '', v)
    return v.rstrip('/')


def _name_substr_related(norm_a: str, norm_b: str) -> bool:
    """两个已规范化作者名是否构成子串关系（较短者是较长者的子串）。

    门槛：较短者含中文 >=3 字、纯英文 >=4 字符——"奶油桃"⊂"奶油桃NaytoTime"
    可识别，而"饭"（1 字）、"水神"（2 字）这类短名不触发，避免大量误报。
    """
    if not norm_a or not norm_b or norm_a == norm_b:
        return False
    short, long_ = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
    if len(short) < 3:
        return False
    if not re.search(r'[\u4e00-\u9fff]', short) and len(short) < 4:
        return False
    return short in long_


def find_merge_candidates() -> list[tuple[str, str, str]]:
    """找重复作者候选 (a, b, 原因)。依据：平台账号相同、规范化名字相等、名字子串。"""
    authors = lib_readme.load_authors_index().get('authors') or {}
    pairs: dict[tuple[str, str], str] = {}  # (小编号, 大编号) -> 原因

    def add_pair(x: str, y: str, reason: str) -> None:
        if x == y:
            return
        a, b = sorted([x, y])
        pairs.setdefault((a, b), reason)

    # 平台相同（QQ/Bilibili/YouTube 等账号一致）
    platform_map: dict[tuple, list[str]] = {}
    for aid, entry in authors.items():
        for key, val in (entry.get('platforms') or {}).items():
            if key.lower() not in ('qq', 'bilibili', 'youtube', 'twitter', 'discord'):
                continue
            platform_map.setdefault((key.lower(), normalize_platform_val(key, val)), []).append(aid)
    for ids in platform_map.values():
        if len(ids) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    add_pair(ids[i], ids[j], f"平台相同: {ids[i]}/{ids[j]}")

    # 规范化名字相等（全角/大小写/# 差异归一后相同）
    name_map: dict[str, list[str]] = {}
    for aid, entry in authors.items():
        for name in entry.get('name') or []:
            norm = lib_readme.normalize_alias(name)
            if norm:
                name_map.setdefault(norm, []).append(aid)
    for norm, ids in name_map.items():
        if len(ids) > 1:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    add_pair(ids[i], ids[j], f"名字相同: {norm}")

    # 名字子串（较短名是较长名的子串，如 奶油桃 ⊂ 奶油桃NaytoTime）
    name_items: list[tuple[str, str]] = []
    for aid, entry in authors.items():
        for name in entry.get('name') or []:
            norm = lib_readme.normalize_alias(name)
            if norm:
                name_items.append((aid, norm))
    for i in range(len(name_items)):
        aid_i, norm_i = name_items[i]
        for j in range(i + 1, len(name_items)):
            aid_j, norm_j = name_items[j]
            if aid_i == aid_j:
                continue
            if _name_substr_related(norm_i, norm_j):
                short = norm_i if len(norm_i) <= len(norm_j) else norm_j
                add_pair(aid_i, aid_j, f"名字子串: {short}")

    return [(a, b, r) for (a, b), r in sorted(pairs.items())]


# 宽松版平台行（复用 lib/readme.py 的 PLATFORM_ANY_LINE_RE：2-4 空格、冒号可选）
PLATFORM_LINE_RE = lib_readme.PLATFORM_ANY_LINE_RE


def _merge_platform_lines(keep_content: str, drop_content: str, keep_readme: Path) -> int:
    """把 drop README Author 段的平台行（分类容器行 + 平台子行）并入 keep README。

    按平台子行键去重（keep 已存在的平台不再并入）；返回并入行数。
    """
    def author_scope(text: str) -> list[str]:
        m = lib_readme.AUTHOR_SECTION_RE.search(text)
        return (m.group(1) if m else text).splitlines()

    keep_lines = author_scope(keep_content)
    drop_lines = author_scope(drop_content)
    have = {m.group(1).strip().lower()
            for m in PLATFORM_LINE_RE.finditer('\n'.join(keep_lines)) if m.group(2).strip()}

    new_lines: list[str] = []
    pending_container: str | None = None
    for line in drop_lines:
        if not line.strip():
            continue
        m = PLATFORM_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        indent = line[:len(line) - len(line.lstrip())]
        is_sub = len(indent.expandtabs(4)) >= 2
        if is_sub:
            if key.lower() in have or not m.group(2).strip():
                continue  # keep 已有该平台或无值：跳过
            if pending_container:
                new_lines.append(pending_container)  # 先输出所属分类容器行
                pending_container = None
            new_lines.append(line.rstrip())
            have.add(key.lower())
        else:
            pending_container = line.rstrip()  # 容器行：等有效子行出现再输出

    if not new_lines:
        return 0
    insert_at = len(keep_content)
    m = lib_readme.AUTHOR_SECTION_RE.search(keep_content)
    if m:
        insert_at = m.start(1) + len(m.group(1).rstrip())
    merged = keep_content[:insert_at] + '\n' + '\n'.join(new_lines) + keep_content[insert_at:]
    keep_readme.write_text(merged, encoding='utf-8')
    return len(new_lines)


def _merge_name_values(existing: str, drop_name: str) -> str:
    """合并两个 Name 值（' | ' 分隔），按规范化别名去重，保留原始顺序。

    防止合并时把 keep 已有的别名（如「炽湮」）重复并入。"""
    parts = [p.strip() for p in f'{existing} | {drop_name}'.split('|') if p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        norm = lib_readme.normalize_alias(p)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(p)
    return ' | '.join(out)


def move_model_dir(model_dir: Path, target_author: str) -> str:
    """把模型目录移到 Models/<target_author>/ 下；处理目标同名/同模型冲突。"""
    dest_author = MODELS_DIR / target_author
    dest = dest_author / model_dir.name
    if dest.exists():
        return f'[冲突] 目标已存在: {dest.relative_to(REPO_ROOT)}'
    # 目标作者下已有同模型（same_model）目录：提示不自动合并（避免误并不同版本）
    if dest_author.is_dir():
        for sub in dest_author.iterdir():
            if sub.is_dir() and sub.name != model_dir.name \
                    and lib_models.same_model(model_dir.name, sub.name):
                return f'[冲突] 目标作者下已有同模型: {sub.relative_to(REPO_ROOT)}'
    dest_author.mkdir(parents=True, exist_ok=True)
    shutil.move(str(model_dir), str(dest))
    return f'[移动] {model_dir.relative_to(REPO_ROOT)} -> {dest.relative_to(REPO_ROOT)}'


def merge_authors(keep: str, drop: str, reason: str) -> str:
    """把 drop 作者合并进 keep：移动模型、并入名字、迁移 co_creators、删除空目录。"""
    keep_dir, drop_dir = MODELS_DIR / keep, MODELS_DIR / drop
    results: list[str] = []

    # 1. 移动模型目录
    if drop_dir.is_dir():
        for model_dir in sorted(drop_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            msg = move_model_dir(model_dir, keep)
            results.append(msg)
        # 2. 并入名字（drop 作者 README 的 Name 值 -> keep README 的 Name 行）
        keep_readme = keep_dir / 'README.md'
        if keep_readme.is_file():
            content = keep_readme.read_text(encoding='utf-8', errors='ignore')
            drop_readme = drop_dir / 'README.md'
            if drop_readme.is_file():
                drop_text = drop_readme.read_text(encoding='utf-8', errors='ignore')
                drop_name = lib_readme.parse_author_name_value(drop_text)
                if drop_name:
                    m = re.search(r'^(?P<indent>\s*-\s*\*\*(?:Name|作者名称)\*\*\s*[:：]\s*)(?P<val>.*)$',
                                  content, re.MULTILINE | re.IGNORECASE)
                    if m:
                        merged = _merge_name_values(m.group('val').strip(), drop_name)
                        content = content[:m.start()] + m.group('indent') + merged + content[m.end():]
                        keep_readme.write_text(content, encoding='utf-8')
                        results.append(f'[名字] {keep}/README.md 并入「{drop_name}」(去重)')
                # 并入平台行（drop Author 段的平台容器行 + 子行；keep 已有键跳过）
                added = _merge_platform_lines(content, drop_text, keep_readme)
                if added:
                    results.append(f'[平台] {keep}/README.md 并入 {added} 条平台行')
        # 3. 删除只剩 README（或空）的被合并作者目录；仍有其他文件则保留并警告
        remaining = [p for p in drop_dir.rglob('*') if p.is_file()]
        if not remaining or (len(remaining) == 1 and remaining[0].name.lower() == 'readme.md'):
            shutil.rmtree(str(drop_dir))
            results.append(f'[删除] 作者目录 Models/{drop}')
        else:
            results.append(f'[保留] Models/{drop} 仍有文件（未删除，需人工处理）')

    # 4. 迁移 co_creators 键 drop/xxx -> keep/xxx
    meta_path = lib_paths.data_path('author-info', 'co_creators.json')
    meta = lib_paths.load_json(meta_path, {})
    migrated = 0
    for key in [k for k in meta if k.startswith(f'{drop}/')]:
        meta[f'{keep}/{key.split("/", 1)[1]}'] = meta.pop(key)
        migrated += 1
    if migrated:
        lib_paths.save_json(meta_path, meta)
        results.append(f'[co_creators] 迁移 {migrated} 条键')

    return f"合并 {drop} -> {keep}（{reason}）\n  " + '\n  '.join(results)


def _count_mergeable_platform_lines(keep_content: str, drop_content: str) -> int:
    """只读计算 drop 中可并入 keep 的平台子行数（与 _merge_platform_lines 同规则，不写盘）。"""
    def author_scope(text: str) -> list[str]:
        m = lib_readme.AUTHOR_SECTION_RE.search(text)
        return (m.group(1) if m else text).splitlines()

    keep_lines = author_scope(keep_content)
    drop_lines = author_scope(drop_content)
    have = {m.group(1).strip().lower()
            for m in PLATFORM_LINE_RE.finditer('\n'.join(keep_lines)) if m.group(2).strip()}

    n = 0
    for line in drop_lines:
        if not line.strip():
            continue
        m = PLATFORM_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        indent = line[:len(line) - len(line.lstrip())]
        if len(indent.expandtabs(4)) >= 2:
            if key.lower() not in have and m.group(2).strip():
                n += 1
                have.add(key.lower())
    return n


def _preview_merge(keep: str, drop: str) -> str:
    """dry-run 预览：只读列出将执行的操作，不产生任何副作用（不移动/不写盘/不删除）。"""
    keep_dir, drop_dir = MODELS_DIR / keep, MODELS_DIR / drop
    results: list[str] = []

    if drop_dir.is_dir():
        for model_dir in sorted(drop_dir.iterdir()):
            if model_dir.is_dir():
                results.append(f'[移动] {model_dir.relative_to(REPO_ROOT)} -> Models/{keep}/')
        keep_readme = keep_dir / 'README.md'
        drop_readme = drop_dir / 'README.md'
        if keep_readme.is_file() and drop_readme.is_file():
            content = keep_readme.read_text(encoding='utf-8', errors='ignore')
            drop_text = drop_readme.read_text(encoding='utf-8', errors='ignore')
            drop_name = lib_readme.parse_author_name_value(drop_text)
            if drop_name:
                results.append(f'[名字] {keep}/README.md 并入「{drop_name}」(去重)')
            n = _count_mergeable_platform_lines(content, drop_text)
            if n:
                results.append(f'[平台] {keep}/README.md 并入 {n} 条平台行')
        # 预览只看 drop 根目录的直接文件：移动子目录后只剩 README 才会被删除
        direct_files = [p for p in drop_dir.iterdir() if p.is_file()]
        if not direct_files or (len(direct_files) == 1 and direct_files[0].name.lower() == 'readme.md'):
            results.append(f'[删除] 作者目录 Models/{drop}')
        else:
            results.append(f'[保留] Models/{drop} 仍有散文件（未删除，需人工处理）')

    return f"合并 {drop} -> {keep}\n  " + '\n  '.join(results)


def _author_display(author_id: str) -> str:
    """作者编号 -> '首名 (编号)'。"""
    authors = lib_readme.load_authors_index().get('authors') or {}
    entry = authors.get(author_id) or {}
    names = entry.get('name') or []
    return f"{names[0] if names else '?'} ({author_id})"


def _rebuild_indexes() -> None:
    """合并后重建集中作者数据与根 README 作者表（drop 作者目录已删，索引需同步）。"""
    for script, args, label in [('models_organize/03_generate_root_readme.py', ['--data'], '作者数据 authors.json'),
                                ('models_organize/03_generate_root_readme.py', ['--author'], '根 README 作者表')]:
        p = REPO_ROOT / '.github' / 'scripts' / script
        if not p.is_file():
            print(f'  [警告] 未找到 {p}，跳过{label}重建')
            continue
        print(f'  重建{label}...')
        subprocess.run([sys.executable, str(p), *args], cwd=REPO_ROOT, check=False)


def _pick_author(authors: dict, prompt: str) -> str | None:
    """交互搜索并选择一位作者，返回编号；取消返回 None。"""
    while True:
        kw = lib_console.ask(prompt).strip()
        if kw.lower() in ('q', 'quit'):
            return None
        if not kw:
            continue
        hits: list[tuple[str, dict]] = []
        for aid, entry in sorted(authors.items()):
            names = entry.get('name') or []
            if kw == aid or any(kw.lower() in n.lower() for n in names):
                hits.append((aid, entry))
        if not hits:
            print('  未找到匹配作者。')
            continue
        print(f'  命中 {len(hits)} 位：')
        for i, (aid, entry) in enumerate(hits, 1):
            names = ' / '.join(entry.get('name') or [])
            print(f"    [{i}] {aid}  {names}")
        sel = lib_console.ask('  选择编号（Enter=重新搜索, q=退出）: ').strip()
        if sel.lower() in ('q', 'quit'):
            return None
        if sel.isdigit() and 1 <= int(sel) <= len(hits):
            return hits[int(sel) - 1][0]
        print('  编号无效。')


def _manual_merge(apply: bool) -> int:
    """交互搜索选择 keep/drop 两位作者并合并；返回合并对数（0 或 1）。"""
    authors = lib_readme.load_authors_index().get('authors') or {}
    if not authors:
        print('authors.json 缺失或为空，先运行 author rebuild 生成')
        return 0
    print('手动合并：先选「保留方」作者，再选「被合并方」作者。')
    keep = _pick_author(authors, '搜索保留方作者（编号或名字关键词，q=取消）: ')
    if keep is None:
        print('已取消。')
        return 0
    drop = _pick_author(authors, '搜索被合并方作者（编号或名字关键词，q=取消）: ')
    if drop is None:
        print('已取消。')
        return 0
    if keep == drop:
        print('两位作者相同，无法合并。')
        return 0
    reason = '手动选择'
    if not apply:
        print(f'  [计划] {_preview_merge(keep, drop)}')
        return 0
    print('  ' + merge_authors(keep, drop, reason))
    return 1


def _pick_merge_mode() -> int | None:
    """让用户选择合并方式：1=自动候选确认，2=手动选择。取消返回 None。"""
    print('请选择合并方式：')
    print('  [1] 显示重复作者候选，逐对确认合并')
    print('  [2] 手动选择两位作者合并')
    ans = lib_console.ask('输入编号（Enter=取消）: ').strip()
    if not ans or ans.lower() in ('q', 'quit'):
        return None
    if ans == '1':
        return 1
    if ans == '2':
        return 2
    print('无效选择，已取消。')
    return None


def _merge_by_candidates(apply: bool) -> int:
    """自动候选：逐对确认合并。返回合并对数。"""
    candidates = find_merge_candidates()
    if not candidates:
        print('合并作者: 未发现重复作者候选。')
        return 0
    print(f'合并作者: 发现 {len(candidates)} 个候选对（平台相同 / 名字相同 / 名字子串）:')
    merged = 0
    seen_drop: set[str] = set()
    for i, (a, b, reason) in enumerate(candidates, 1):
        if a in seen_drop or b in seen_drop:
            continue  # 已作为被合并方处理
        a_name = _author_display(a)
        b_name = _author_display(b)
        ans = lib_console.ask(f"[{i}/{len(candidates)}] 合并 {b}({b_name}) -> {a}({a_name})？"
                              f"（{reason}） (y=合并, s=反向, n=跳过, q=退出): ").lower()
        if ans in ('q', 'quit'):
            break
        if ans in ('s', 'swap'):
            a, b = b, a
        elif ans not in ('y', 'yes'):
            continue
        if not apply:
            print(f'  [计划] {_preview_merge(a, b)}')
            continue
        print('  ' + merge_authors(a, b, reason))
        merged += 1
        seen_drop.add(b)
    return merged


def _merge_manual_loop(apply: bool) -> int:
    """手动选择合并：可反复，直到退出。返回合并对数。"""
    merged = 0
    while True:
        ans = lib_console.ask('手动选择两位作者合并？(y=开始, q=退出): ').strip().lower()
        if ans in ('q', 'quit', 'n', 'no'):
            break
        if ans not in ('y', 'yes'):
            continue
        merged += _manual_merge(apply)
    return merged


def merge_authors_flow(apply: bool) -> int:
    """合并重复作者：先选方式（自动候选确认 / 手动选择），再执行。返回合并对数。"""
    mode = _pick_merge_mode()
    if mode is None:
        print('已取消。')
        return 0
    merged = _merge_by_candidates(apply) if mode == 1 else _merge_manual_loop(apply)
    if merged and apply:
        _rebuild_indexes()
    print(f'合并作者: 共 {merged} 对已合并' if apply else 'dry-run: 未执行')
    return merged


# ---------------------------------------------------------------------------
# 合并手动维护的作者信息（author merge-info）
# ---------------------------------------------------------------------------
def merge_author_info(input_path: Path, apply: bool = False) -> int:
    """从手动维护的信息文件合并作者信息（平台/团队/别名）进 authors.json。

    输入 JSON 形如 {作者名: {platforms: {...}, team: "团队名", aliases: [...]}}，
    键可用编号（如 "0045"）或任一作者名/别名（# 前缀可选），按规范化名匹配。
    合并规则（幂等，不覆盖已有手写内容）：
      platforms: 只补缺失的 http(s) 平台键（已有键不覆盖）；
      team:      非空才写入；
      aliases:   追加并与现有 name 规范化去重。
    默认 dry-run 只报告计划；--apply 才写回 authors.json。返回更新的作者数。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.get('authors') if isinstance(data, dict) else None
    if not authors:
        print('authors.json 缺失或为空，先运行 author rebuild 生成')
        return 0
    try:
        updates = json.loads(input_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        print(f'输入文件无法解析或不存在: {input_path}')
        return 0
    if not isinstance(updates, dict) or not updates:
        print(f'输入文件无有效作者信息: {input_path}')
        return 0
    matched, unmatched = merge_author_updates(authors, updates)
    if not matched and not unmatched:
        print('输入信息与 authors.json 无差异（无新增/变更）。')
        return 0
    for aid, changes in matched:
        entry = authors[aid]
        print(f'  {aid}  {" | ".join(entry.get("name") or [])}')
        print(f'      -> {"、".join(changes)}')
    for key in unmatched:
        print(f'  [未匹配] {key}（authors.json 无此作者/别名，未合并）')
    if not apply:
        print(f'dry-run: 共 {len(matched)} 位作者待更新，{len(unmatched)} 个未匹配（加 --apply 写入）')
        return 0
    lib_paths.save_json(path, data)
    print(f'已合并 {len(matched)} 位作者信息 -> {lib_paths.get_safe_relpath(path)}')
    return len(matched)


def merge_author_updates(authors: dict,
                         updates: dict,
                         overwrite: bool = False) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """把手动信息 {键: {platforms, team, aliases, tags}} 按编号或规范化名匹配合并进 authors。

    匹配：键=编号直接命中，否则按规范化别名；未匹配的键进 unmatched。
    合并模式 (overwrite=False) 幂等，不覆盖已有手写内容：
      platforms: 只补缺失的平台键（含 QQ 号等纯文本值；已有键不覆盖）；
      team:      非空才写入；
      aliases:   追加并与现有 name 规范化去重；
      badges:    追加并与现有 badges 小写去重。
    覆盖模式 (overwrite=True) 以 updates 为权威：
      platforms: 用 updates 值覆盖（新增/更新，含 QQ 号等纯文本值），JSON 有但
                 updates 无的键保留；
      team:      非空时覆盖（含更新已有值），空值不清除；
      badges:    仅当 updates 有 badges 时才替换（无 badges 不删除，保留自动徽章）。
    """
    alias_index: dict[str, str] = {}
    for aid, entry in authors.items():
        for n in entry.get('name') or []:
            norm = lib_readme.normalize_alias(n)
            if norm:
                alias_index.setdefault(norm, aid)

    matched: list[tuple[str, list[str]]] = []   # (编号, 变更描述)
    unmatched: list[str] = []
    for key, upd in updates.items():
        if not isinstance(upd, dict):
            print(f'  跳过 {key!r}：值不是对象')
            continue
        aid = key if key in authors else alias_index.get(lib_readme.normalize_alias(key))
        if aid is None:
            unmatched.append(key)
            continue
        entry = authors[aid]
        changes: list[str] = []
        if overwrite:
            # 全量覆盖模式：以 updates 为权威，目标中有但来源无的字段删除
            # name：用 README 的 Name 行（aliases）全量覆盖 names
            names_upd = [str(n).strip().lstrip('#＃').strip()
                         for n in (upd.get('aliases') or [])
                         if isinstance(n, str) and n.strip()]
            if names_upd:
                if entry.get('name') != names_upd:
                    entry['name'] = names_upd
                    changes.append(f'name覆盖={len(names_upd)}')
            # platforms：完全替换（来源无则删除）；保留纯文本值（如 QQ 号）
            new_plat = {k: v for k, v in (upd.get('platforms') or {}).items()
                        if isinstance(v, str) and v.strip()}
            old_plat = entry.get('platforms') or {}
            if new_plat != old_plat:
                if new_plat:
                    entry['platforms'] = new_plat
                else:
                    entry.pop('platforms', None)
                changes.append('platforms覆盖')
            # team：覆盖或删除
            team = str(upd.get('team') or '').strip()
            if team:
                if entry.get('team') != team:
                    entry['team'] = team
                    changes.append(f'team覆盖={team}')
            elif 'team' in entry:
                del entry['team']
                changes.append('team删除')
            # tags→badges：当 README 有 badges 时才替换（保留自动徽章；无 badges 不删除）
            badges_upd = [str(b).strip().lower() for b in (upd.get('badges') or [])
                          if isinstance(b, str) and b.strip()]
            if badges_upd:
                if entry.get('badges') != badges_upd:
                    entry['badges'] = badges_upd
                    changes.append('badges覆盖')
            # 无 badges 行时保留 JSON 原值（自动徽章/手写 badges 不丢失）
        else:
            # 平台：只补缺失的键（含 QQ 号等纯文本值）；同键不同值 -> 追加 pkey-N 新键
            for pkey, pval in (upd.get('platforms') or {}).items():
                if not (isinstance(pval, str) and pval.strip()):
                    continue
                plat = entry.setdefault('platforms', {})
                if pkey not in plat:
                    plat[pkey] = pval
                    changes.append(f'平台+{pkey}')
                elif plat[pkey] != pval:
                    # 同平台键不同值：新增 pkey-1 / pkey-2 … 保留两个值
                    n = 1
                    while f'{pkey}-{n}' in plat:
                        n += 1
                    plat[f'{pkey}-{n}'] = pval
                    changes.append(f'平台+{pkey}-{n}')
            # 团队：非空才写入
            team = str(upd.get('team') or '').strip()
            if team and entry.get('team') != team:
                entry['team'] = team
                changes.append(f'team={team}')
            # 别名：追加并规范化去重
            extra = [str(a).strip() for a in (upd.get('aliases') or [])
                     if isinstance(a, str) and a.strip()]
            if extra:
                existing = {lib_readme.normalize_alias(n) for n in (entry.get('name') or [])}
                to_add = [a for a in extra
                          if lib_readme.normalize_alias(a)
                          and lib_readme.normalize_alias(a) not in existing]
                if to_add:
                    entry.setdefault('name', []).extend(to_add)
                    changes.append(f'别名+{len(to_add)}')
            # 徽章：追加并小写去重（自动判定落盘 / README 手写反向合并都走这里）
            extra_badges = [str(b).strip().lower() for b in (upd.get('badges') or [])
                            if isinstance(b, str) and b.strip()]
            if extra_badges:
                existing_badges = {str(b).lower() for b in (entry.get('badges') or [])}
                to_add_badges = [b for b in extra_badges if b not in existing_badges]
                if to_add_badges:
                    entry.setdefault('badges', []).extend(to_add_badges)
                    changes.append(f'徽章+{len(to_add_badges)}')
        if changes:
            matched.append((aid, changes))
    return matched, unmatched
