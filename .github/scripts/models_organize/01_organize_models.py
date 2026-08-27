
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 模型归档工具（重构版）——按作者/作品将待归档的 .ysm 归位到仓库。

流程（6 层）：
  ① 数据层   收集输入 / 加载作者索引、角色库、作品键表
  ② 解析层   解析 .ysm → 模型元数据 + 作者块；登记/合并作者 → authors.json
  ③ 决策层   命名 / 归类 / 去重 / 版本化 / 合并
  ④ 执行层   移动 / 复制 / 创建目录
  ⑤ 联动层   --with-* 调下游
  ⑥ 入口层   main 编排

规则：
  - 有作者 → 登记/合并 authors.json（新作者从 0000 起取空缺编号 + 升序 + 建目录；
    旧作者补缺合并：别名去重追加末尾 / 平台补缺失 http 键），主作者 move、其他模型作者 copy
  - 无作者 → 按作品归类到 Other-YSM-Models/<作品>/（未匹配 → Unknown）
  - 命名：resolve_folder_name —— 优先用文件内 <name>（无则文件名）作匹配主体，resolve_name3 匹配出
    作品则标准化（作品/角色，其余字段原位）；匹配不出则兜底用内部 <name> 命名（去装饰符号）。
    （产出 <作品>_<中文角色>[_皮肤]_<英文角色>[_皮肤]_<评级>）
  - 去重：sha256 内容相同跳过；同名文件夹按文件大小版本化；同模型多版本合并
  - 不写 co_creators.json（co-creator 作者丢弃）

用法:
  python .github/scripts/models_organize/01_organize_models.py <文件或目录>... [选项]

选项:
  --apply                真正执行（默认 dry-run）
  --root PATH            指定仓库根目录（默认自动检测 cwd/脚本位置）
  --with-gen-readmes     归档成功后生成模型 README
  --verbose              打印匹配细节
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 正常导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import models as lib_models
from lib import paths as lib_paths
from lib import readme as lib_readme
from lib import ysm as lib_ysm
from lib.author_readme import format_author_name
from lib.ysm import _maker_level, _is_oc_role

# ---- lib 绑定 ----
extract_metadata = lib_ysm.extract_metadata
classify_authors = lib_ysm.classify_authors
normalize_alias = lib_readme.normalize_alias
build_author_index = lib_readme.build_author_index
find_author = lib_readme.find_author
match_author_id = lib_readme.match_author_id
find_workspace_root = lib_paths.find_workspace_root
has_cjk = lib_models.has_cjk
normalize_name_for_cmp = lib_models.normalize_name_for_cmp
clean_file_stem = lib_models.clean_file_stem
same_model = lib_models.same_model
detect_work_prefix = lib_models.detect_work_prefix

ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TRAILING_DOT_SPACE_RE = re.compile(r'[.\s]+$')
WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10))
}


# ===========================================================================
# ① 数据与工具层
# ===========================================================================
def _work_map(root: Path) -> dict[str, str]:
    char_dir = lib_paths.CHARACTER_DIR
    if root and root != lib_paths.WORKSPACE_ROOT:
        char_dir = root / '.github' / 'data' / 'model-info' / 'character'
    if not char_dir.is_dir():
        return {}
    return {f.stem.upper(): f.stem for f in char_dir.glob('*.json')}


def collect_ysm_files(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for inp in inputs:
        if inp.is_file() and inp.suffix.lower() == '.ysm':
            files.append(inp)
        elif inp.is_dir():
            found = list(inp.rglob('*.ysm')) + list(inp.rglob('*.YSM'))
            files.extend(f for f in found if f.is_file())
        else:
            print(f"[错误] 输入不存在或非 .ysm 文件: {inp}", file=sys.stderr)
    
    seen: set[str] = set()
    unique: list[Path] = []
    for f in files:
        key = os.path.normcase(str(f.resolve()))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return sorted(unique)


def sanitize_folder_name(name: str) -> str:
    name = ILLEGAL_CHARS_RE.sub('_', name)
    name = TRAILING_DOT_SPACE_RE.sub('', name)
    name = name.strip()
    if not name:
        name = 'unnamed_model'
    if name.upper() in WINDOWS_RESERVED:
        name = '_' + name
    return name


def resolve_folder_name(inner_name: str | None, file_stem: str) -> str:
    name = (inner_name or '').strip()
    if not name:
        name = clean_file_stem(file_stem)
    if not name:
        name = 'unnamed_model'
    return sanitize_folder_name(name)


def file_sha256(path: Path) -> str:
    if not path.is_file():
        return ''
    h = hashlib.sha256()
    try:
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1 << 16), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


# ===========================================================================
# ② 解析与作者索引层
# ===========================================================================
def next_free_author_id(models_dir: Path, reserved_ids: set[str] | None = None) -> str:
    """取下一个空缺作者编号：扫描作者目录根下 '<编号>' 或 '<编号>-<名称>' 目录。

    作者目录在仓库根（'0001-02Bunny,蓝玫瑰' 或新作者的裸 '0207'），编号 = 目录前 4 位；
    已重编号连续时，新作者取当前最大编号 + 1。
    """
    existing = {
        int(d.name[:4])
        for d in models_dir.iterdir()
        if d.is_dir() and re.fullmatch(r'\d{4}(-.*)?', d.name)
    }
    if reserved_ids:
        for rid in reserved_ids:
            if rid.isdigit():
                existing.add(int(rid))
    i = 0
    while i in existing:
        i += 1
    return f'{i:04d}'


def author_dir_at(root: Path, aid: str) -> Path:
    """返回作者目录：已存在 '<编号>-<名称>' 用现有目录，否则待建裸 '<编号>'。"""
    prefix = f'{aid}-'
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name.startswith(prefix):
            return d
    return root / aid


def register_author(models_dir: Path, author_id: str, name: str,
                    platforms: dict | None = None) -> None:
    """登记新作者到 authors.json，并在作者目录根（仓库根）建裸编号目录。

    目录先以裸编号创建（如 0207/），作者名等 README 生成后由
    06_rename_author_dirs.py 对齐为 '<编号>-<作者名[,别名]>'。
    """
    path = lib_paths.data_path('author-info', 'authors.json')
    data = lib_paths.load_json(path, {})
    authors = data.setdefault('authors', {})
    clean_name = sanitize_folder_name(name)
    authors[author_id] = {
        'name': [name],
        'readme': f'{author_id}-{clean_name}/README.md',
        'platforms': platforms or {},
    }
    authors = dict(sorted(authors.items(), key=lambda kv: int(kv[0])))
    data['authors'] = authors
    lib_paths.save_json(path, data)
    # author_dir_at 已返回正确目标：已有 '<编号>-<名称>' 目录则复用，否则待建裸 '<编号>'
    target = author_dir_at(models_dir, author_id)
    target.mkdir(parents=True, exist_ok=True)


def resolve_and_register_author(block: dict, alias_to_id: dict, runtime_index: dict,
                                root: Path, models_dir: Path, apply: bool,
                                verbose: bool = False,
                                reserved_ids: set[str] | None = None) -> tuple[str, bool]:
    author_id, _ = match_author_id(block['name'], alias_to_id, runtime_index, verbose)
    if author_id:
        return author_id, False
    
    key = normalize_alias(block['name'])
    if key and key in runtime_index:
        return runtime_index[key], False

    new_id = next_free_author_id(models_dir, reserved_ids)
    if key:
        runtime_index[key] = new_id
    return new_id, True


# ===========================================================================
# ③ 目录与精准归档决策层
# ===========================================================================
def find_target_dest_dir(target_author_dir: Path, folder_name: str, input_sha: str) -> tuple[Path, bool]:
    if not target_author_dir.is_dir():
        return target_author_dir / folder_name, False

    for sub in target_author_dir.iterdir():
        if sub.is_dir() and not sub.name.startswith('.'):
            for ysm in sub.rglob('*'):
                if ysm.is_file() and ysm.suffix.lower() == '.ysm':
                    if file_sha256(ysm) == input_sha:
                        return sub, True

    norm = normalize_name_for_cmp(folder_name)
    for sub in sorted(target_author_dir.iterdir()):
        if sub.is_dir() and not sub.name.startswith('.'):
            sub_norm = normalize_name_for_cmp(sub.name)
            if sub_norm in ('unknown',):
                continue
            if sub_norm == norm or same_model(folder_name, sub.name):
                return sub, False

    return target_author_dir / folder_name, False


def sync_files_incremental(src_item: Path, dest_target: Path, apply: bool) -> tuple[bool, str]:
    if src_item.is_file():
        if not dest_target.exists():
            if apply:
                dest_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_item), str(dest_target))
            return True, f"补全文件 -> {dest_target.name}"
        else:
            return False, f"文件已存在: {dest_target.name}"

    elif src_item.is_dir():
        has_changes = False
        if apply:
            dest_target.mkdir(parents=True, exist_ok=True)
        for sub in list(src_item.iterdir()):
            sub_dest = dest_target / sub.name
            changed, _ = sync_files_incremental(sub, sub_dest, apply)
            if changed:
                has_changes = True
        return has_changes, f"同步目录 -> {dest_target.name}/"

    return False, ""


def archive_model_bundle(path: Path, target_dir: Path, folder_name: str,
                         apply: bool, root: Path, verbose: bool) -> tuple[str, list[Path]]:
    input_sha = file_sha256(path)
    dest_dir, content_exists = find_target_dest_dir(target_dir, folder_name, input_sha)
    
    source_dir = path.parent
    all_ysms = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == '.ysm'] if source_dir.is_dir() else [path]
    is_single_model_dir = bool(all_ysms and all(same_model(all_ysms[0].stem, y.stem) for y in all_ysms))

    actions_taken = []
    synced_items: list[Path] = []
    
    # 1. 同步 .ysm 主文件
    dest_ysm = dest_dir / path.name
    if content_exists:
        actions_taken.append(f"模型 .ysm 已存在于 {dest_dir.name}（跳过写入）")
    else:
        if not apply:
            actions_taken.append(f"[计划] 写入 .ysm -> {dest_dir.relative_to(root)}/{path.name}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            if not dest_ysm.exists():
                shutil.copy2(str(path), str(dest_ysm))
                actions_taken.append(f"已同步模型 -> {dest_ysm.name}")
            else:
                # 同名不同内容：写入递增的衍生版本号，避免覆盖已存在的历史版本
                v_index = 2
                while True:
                    v_name = f"{path.stem}_v{v_index}{path.suffix}"
                    v_dest = dest_dir / v_name
                    if not v_dest.exists():
                        break
                    v_index += 1
                shutil.copy2(str(path), str(v_dest))
                actions_taken.append(f"同名不同内容 -> 衍生版本 {v_name}")

    # 2. 同步伴随附属资源
    if is_single_model_dir and source_dir.is_dir():
        for item in list(source_dir.iterdir()):
            if item.name.startswith('.') or item.suffix.lower() == '.ysm':
                continue
            
            target_item = dest_dir / item.name
            changed, msg = sync_files_incremental(item, target_item, apply)
            synced_items.append(item)
            if changed or verbose:
                actions_taken.append(f"    [附属资源] {msg}")

    for action in actions_taken:
        print(f"  {action}")

    return 'done', synced_items


# ===========================================================================
# ④ 联动与入口层
# ===========================================================================
def _run_script(root: Path, rel: str, args: list[str], label: str) -> None:
    script = root / '.github' / 'scripts' / rel
    if not script.is_file():
        print(f"  [警告] 未找到 {script}，跳过{label}")
        return
    print(f"  {label}...")
    subprocess.run([sys.executable, str(script), *args], cwd=root, check=False)


def run_generate_model_readmes(root: Path) -> None:
    _run_script(root, 'models_organize/03_generate_model_readmes.py', [], '生成模型 README')


def cleanup_source_folder(path: Path, synced_items: list[Path], input_roots: set[Path]) -> None:
    """清理已归档的源文件及附属资源，并按规则保护输入根目录。"""
    source_dir = path.parent
    path.unlink(missing_ok=True)

    for item in set(synced_items):
        if item.is_file():
            item.unlink(missing_ok=True)
        elif item.is_dir():
            shutil.rmtree(str(item), ignore_errors=True)

    if not source_dir.is_dir():
        return

    # 规则：若当前源目录属于用户直接输入的路径，则保留空文件夹，不执行删除
    is_protected_root = source_dir.resolve() in input_roots

    remaining = [p for p in source_dir.iterdir() if not p.name.startswith('.')]
    if not remaining and not is_protected_root:
        shutil.rmtree(str(source_dir), ignore_errors=True)


def process_file(path: Path, root: Path, alias_to_id: dict, runtime_index: dict,
                 work_map: dict, rejected_author_names: set[str], input_roots: set[Path],
                 apply: bool, verbose: bool) -> dict:
    if not path.exists():
        return {'action': 'skipped', 'reason': '文件已被前面的操作处理', 'new_author': False}

    rel = path.relative_to(root) if path.is_relative_to(root) else path
    print(f"\n== {rel} ==")
    result = {'action': 'skipped', 'reason': '', 'new_author': False}

    meta = extract_metadata(path)
    if not meta:
        result['reason'] = '文件读取失败'
        return result
        
    inner_name = meta.get('name') or ''
    blocks = meta.get('author_blocks') or []
    # 作者目录在仓库根（'<编号>-<名称>' 或裸 '<编号>'），归档目标在此
    models_dir = root

    model_blocks = [b for b in blocks if _maker_level(b.get('role') or '') in (1, 2)]
    if not model_blocks and blocks:
        model_blocks = [blocks[0]]

    valid_blocks = [b for b in model_blocks if b.get('name', '').strip() not in rejected_author_names]

    if not valid_blocks:
        folder_name = resolve_folder_name(inner_name, path.stem)
        sub = detect_work_prefix(folder_name, work_map) or 'Unknown'
        target_dir = root / 'Other-YSM-Models' / sub
        print(f"  未指定有效作者，按作品分类 -> Other-YSM-Models/{sub}")
        print(f"  模型目标文件夹: {folder_name}")
        
        if apply:
            ans = input(f"  是否归档/同步此模型及其附属文件？(y/n): ").strip().lower()
            if ans != 'y':
                print("  跳过")
                return result
        
        _, synced_items = archive_model_bundle(path, target_dir, folder_name, apply, root, verbose)
        if apply:
            cleanup_source_folder(path, synced_items, input_roots)
        result['action'] = 'done'
        return result

    primary = valid_blocks[0]
    print(f"  作者列表: " + ', '.join(f"{b['name']}" for b in blocks))
    print(f"  模型创建者: {primary['name']}（共 {len(valid_blocks)} 个有效作者）")

    targets: list[tuple[str, str, dict]] = []
    for block in valid_blocks:
        aid, is_new = resolve_and_register_author(block, alias_to_id, runtime_index,
                                                  root, models_dir, apply, verbose)
        if is_new:
            print(f"  [新作者] {block['name']} → 编号 {aid}")
            result['new_author'] = True
        else:
            db_entry = alias_to_id.get(normalize_alias(block['name']))
            db_name = db_entry if db_entry else block['name']
            print(f"  [已有作者] 模型: {block['name']} → 数据库: {aid} ({db_name})")
        
        mode = 'move' if block is primary else 'copy'
        targets.append((aid, mode, block))

    dedup: dict[str, tuple[str, dict]] = {}
    for aid, mode, block in targets:
        if aid not in dedup or (mode == 'move' and dedup[aid][0] != 'move'):
            dedup[aid] = (mode, block)

    folder_name = resolve_folder_name(inner_name, path.stem)
    print(f"  模型目标文件夹: {folder_name}")

    if apply:
        target_desc = [f"{aid}({b[1]['name']})" for aid, b in dedup.items()]
        print(f"  目标作者: {', '.join(target_desc)}")
        ans = input(f"  是否归档/同步此模型及其附属资源？(y/n): ").strip().lower()
        if ans != 'y':
            print("  跳过")
            return result

    all_synced_items: list[Path] = []
    for aid in dedup:
        # 归档到作者目录（现有 '<编号>-<名称>' 或待建裸 '<编号>'）
        target_author_dir = author_dir_at(models_dir, aid)
        _, synced_items = archive_model_bundle(path, target_author_dir, folder_name,
                                               apply, root, verbose)
        all_synced_items.extend(synced_items)

    if apply:
        cleanup_source_folder(path, all_synced_items, input_roots)

    result['action'] = 'done'
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('inputs', nargs='+', help='.ysm 文件或目录（目录递归收集 *.ysm）')
    parser.add_argument('--apply', action='store_true', help='真正执行（默认 dry-run）')
    parser.add_argument('--root', metavar='PATH', default=None, help='仓库根目录（默认自动检测）')
    parser.add_argument('--with-gen-readmes', action='store_true', help='归档成功后生成模型 README')
    parser.add_argument('--verbose', action='store_true', help='打印匹配与同步细节')
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else find_workspace_root()
    # 作者目录在仓库根（'<编号>-<名称>' 或裸 '<编号>'），Models/ 目录已不再使用
    models_dir = root
    if not models_dir.is_dir():
        print(f"错误: {models_dir} 目录不存在（可用 --root 指定仓库根目录）", file=sys.stderr)
        return 2

    alias_to_id, id_to_name = build_author_index(models_dir, root / 'README.md')
    print(f"作者索引: {len(alias_to_id)} 个别名 / {len(id_to_name)} 位作者")
    work_map = _work_map(root)
    print(f"作品键表: {len(work_map)} 个")

    raw_inputs = [Path(x).resolve() for x in args.inputs]
    input_roots = {p if p.is_dir() else p.parent for p in raw_inputs}

    files = collect_ysm_files(raw_inputs)
    if not files:
        print("没有可处理的 .ysm 文件。")
        return 1

    mode = "执行" if args.apply else "预览（dry-run，加 --apply 执行）"
    print(f"模式: {mode} | 共 {len(files)} 个文件")

    new_authors: dict[str, dict] = {}
    runtime_index: dict[str, str] = {}
    reserved_ids: set[str] = set()

    for f in files:
        meta = extract_metadata(f)
        if not meta:
            continue
        blocks = meta.get('author_blocks') or []
        model_blocks = [b for b in blocks if _maker_level(b.get('role') or '') in (1, 2)]
        if not model_blocks and blocks:
            model_blocks = [blocks[0]]
            
        for block in model_blocks:
            aid, is_new = resolve_and_register_author(
                block, alias_to_id, runtime_index, root, models_dir,
                args.apply, args.verbose, reserved_ids=reserved_ids
            )
            if is_new:
                reserved_ids.add(aid)
                if aid not in new_authors:
                    name = block.get('name', '').strip()
                    contacts = block.get('contacts') or {}
                    new_authors[aid] = {'name': name, 'platforms': dict(contacts)}

    rejected_author_names: set[str] = set()
    if new_authors and args.apply:
        print(f"\n== 发现 {len(new_authors)} 个新作者，请逐一确认 ==")
        for aid in sorted(new_authors, key=int):
            info = new_authors[aid]
            print(f"\n  新作者编号: {aid}")
            print(f"  模型中的名称: {info['name']}")
            print(f"  平台信息: {info['platforms']}")
            ans = input(f"  是否创建此作者？(y/n/edit): ").strip().lower()
            if ans == 'n':
                rejected_author_names.add(info['name'])
                print(f"  跳过作者 {aid}（后续相关模型将归入 Other-YSM-Models）")
                continue
            elif ans == 'edit':
                edited_name = input(f"  输入作者名称（留空保持 [{info['name']}]）: ").strip()
                if edited_name:
                    info['name'] = edited_name
                print(f"  当前平台: {info['platforms']}")
                edit_platforms = input(f"  修改平台？(y/n): ").strip().lower()
                if edit_platforms == 'y':
                    new_platforms = {}
                    while True:
                        pk = input(f"  平台键（如 Bilibili，留空结束）: ").strip()
                        if not pk:
                            break
                        pv = input(f"  平台值（如 https://...）: ").strip()
                        if pv:
                            new_platforms[pk] = pv
                    if new_platforms:
                        info['platforms'] = new_platforms

            register_author(models_dir, aid, info['name'], info['platforms'])
            print(f"  已创建作者 {aid}（{info['name']}）")

    processed = skipped = 0
    for f in files:
        res = process_file(
            f, root, alias_to_id, runtime_index, work_map,
            rejected_author_names, input_roots, args.apply, args.verbose
        )
        if res['action'] == 'done':
            processed += 1
        elif res['reason']:
            skipped += 1

    if args.apply and processed > 0:
        if args.with_gen_readmes:
            run_generate_model_readmes(root)

    print("\n" + "=" * 50)
    print(f"完成: 归档/增量同步 {processed} 个任务，跳过 {skipped} 个文件（{mode}）")
    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    raise SystemExit(main())