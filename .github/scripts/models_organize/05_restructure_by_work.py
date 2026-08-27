#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按作品重组模型目录结构（方案C）。

执行流程（分阶段，--apply 才真正写盘）：
  1. 数据分析：扫描当前 Models/，提取作品缩写、作者信息
  2. 创建数据层：work-info/works.json + model-info/models.json
  3. 物理迁移：创建 Models/<作品>/ 目录，移动模型文件夹
  4. 清理：删除空作者目录，更新路径配置

用法：
  python .github/scripts/models_organize/05_restructure_by_work.py [--apply] [--stage 1-4]

规则：
  - 未在 character/*.json 中注册的作品缩写 → 归入 _unknown/
  - 模型文件夹名去掉作品缩写前缀（BA_Hoshino_星野_LA → Hoshino_星野_LA）
  - 真正重复的模型（同一文件夹名、同一作者）只保留一份，作者信息合并
"""

import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

# 把 .github/scripts 加回 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import paths as lib_paths  # noqa: E402

REPO_ROOT = lib_paths.WORKSPACE_ROOT
MODELS_DIR = REPO_ROOT / 'Models'
DATA_DIR = REPO_ROOT / '.github' / 'data'
AUTHOR_INFO_DIR = DATA_DIR / 'author-info'
MODEL_INFO_DIR = DATA_DIR / 'model-info'
CHARACTER_DIR = MODEL_INFO_DIR / 'character'
WORK_INFO_DIR = DATA_DIR / 'work-info'  # 新建目录

# 模型文件夹名中的等级后缀
RATING_SUFFIXES = ('_LA', '_LB', '_LC', '_LD')


def load_json_safe(path: Path, default=None):
    """安全加载 JSON，失败时返回 default。"""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    """写 JSON（UTF-8、ensure_ascii=False、末尾换行），自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_known_works() -> set:
    """从 character/*.json 中加载已定义的作品缩写集合。"""
    known = set()
    if not CHARACTER_DIR.is_dir():
        return known
    for cf in sorted(CHARACTER_DIR.glob('*.json')):
        data = load_json_safe(cf)
        if data and 'work' in data:
            abbr = data['work'].get('abbr', cf.stem)
            known.add(abbr)
    return known


def parse_model_folder_name(name: str) -> dict:
    """解析模型文件夹名，返回 {work, en_name, zh_name, rating, base_name}。

    命名格式：<WORK>_<英文名>_<中文名>_[LA|LB|LC|LD]
    示例：BA_Hoshino_星野_LA → work=BA, en_name=Hoshino, zh_name=星野, rating=LA
    特殊情况：部分文件夹没有中文名或等级后缀。
    """
    result = {'work': None, 'en_name': '', 'zh_name': '', 'rating': None, 'base_name': name}

    # 提取等级后缀
    rating = None
    for suffix in RATING_SUFFIXES:
        if name.endswith(suffix):
            rating = suffix[1:]  # 去掉前导下划线
            name = name[:-len(suffix)]
            break

    # 分割作品缩写（第一个下划线之前的部分）
    if '_' in name:
        parts = name.split('_', 1)
        result['work'] = parts[0]
        rest = parts[1]
        # 剩下的部分：英文名_中文名（也可能没有中文名）
        if '_' in rest:
            sub_parts = rest.split('_', 1)
            result['en_name'] = sub_parts[0]
            result['zh_name'] = sub_parts[1]
        else:
            result['en_name'] = rest
    else:
        result['work'] = name

    result['rating'] = rating
    result['base_name'] = name  # 不含等级后缀的原始名
    return result


def get_new_folder_name(parsed: dict) -> str:
    """生成新文件夹名：去掉作品缩写前缀，保留等级后缀。

    例如：BA_Hoshino_星野_LA → Hoshino_星野_LA
    """
    work = parsed['work']
    base = parsed['base_name']
    rating = parsed['rating']

    # 去掉作品缩写前缀（如果 base 以 work 开头）
    if base.startswith(work + '_'):
        stem = base[len(work) + 1:]
    else:
        stem = base

    if rating:
        return f'{stem}_{rating}'
    return stem


def scan_models(known_works: set) -> dict:
    """扫描 Models/ 下所有作者目录和模型，返回结构化数据。

    未在 known_works 中注册的作品缩写将归入 'Unknown'。
    """
    result = {
        'authors': {},
        'models': [],
        'works': defaultdict(lambda: {'models': [], 'authors': set()}),
        'duplicates': defaultdict(list),
        'stats': {'author_dirs': 0, 'model_folders': 0, 'unknown_work': 0},
    }

    if not MODELS_DIR.is_dir():
        print(f'[错误] Models 目录不存在: {MODELS_DIR}')
        return result

    # 加载作者信息
    authors_data = load_json_safe(AUTHOR_INFO_DIR / 'authors.json', {})
    authors_dict = authors_data.get('authors', {}) if isinstance(authors_data, dict) else {}

    author_dirs = sorted([
        d for d in MODELS_DIR.iterdir()
        if d.is_dir() and d.name.isdigit() and len(d.name) == 4
    ])
    result['stats']['author_dirs'] = len(author_dirs)

    print(f'扫描 {len(author_dirs)} 个作者目录...')

    for author_dir in author_dirs:
        author_id = author_dir.name
        author_info = authors_dict.get(author_id, {})
        author_name = (
            author_info.get('name', [author_id])[0]
            if isinstance(author_info.get('name'), list) else author_id
        )
        result['authors'][author_id] = {
            'name': author_name,
            'platforms': author_info.get('platforms', {}),
            'badges': author_info.get('badges', []),
        }

        model_dirs = sorted([
            d for d in author_dir.iterdir()
            if d.is_dir() and not d.name.lower().startswith('readme')
            and d.name not in ('previews', 'info', '.git')
        ])

        for model_dir in model_dirs:
            folder_name = model_dir.name
            parsed = parse_model_folder_name(folder_name)
            raw_work = parsed['work'] or 'Unknown'

            # ★ 关键规则：未注册的作品缩写 → Unknown
            if raw_work not in known_works:
                work = 'Unknown'
            else:
                work = raw_work

            # 收集文件信息
            files = []
            for f in model_dir.iterdir():
                if f.is_file() and not f.name.startswith('.') and f.suffix.lower() not in (
                    '.md', '.txt', '.html', '.json', '.py', '.webp'
                ):
                    files.append({
                        'name': f.name,
                        'size': f.stat().st_size,
                        'suffix': f.suffix.lower(),
                    })

            # 收集预览图
            preview_dir = model_dir / 'previews'
            previews = []
            if preview_dir.is_dir():
                for f in sorted(preview_dir.iterdir()):
                    if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                        previews.append(f.name)

            model_entry = {
                'author_id': author_id,
                'author_name': author_name,
                'folder_name': folder_name,
                'work': work,
                'raw_work': raw_work,
                'en_name': parsed['en_name'],
                'zh_name': parsed['zh_name'],
                'rating': parsed['rating'],
                'base_name': parsed['base_name'],
                'new_folder_name': get_new_folder_name(parsed),
                'files': files,
                'file_count': len(files),
                'total_size': sum(f['size'] for f in files),
                'previews': previews,
                'preview_count': len(previews),
                'model_readme': (model_dir / 'README.md').is_file(),
            }

            result['models'].append(model_entry)
            result['works'][work]['models'].append(model_entry)
            result['works'][work]['authors'].add(author_id)

            # 构建去重键：作品 + 新文件夹名 + 作者
            dedup_key = f"{work}|{model_entry['new_folder_name']}|{author_id}"
            result['duplicates'][dedup_key].append(model_entry)

    # 统计
    result['stats']['model_folders'] = len(result['models'])
    result['stats']['unknown_work'] = len(result['works'].get('Unknown', {}).get('models', []))

    return result


def stage1_analyze(scan_result: dict, known_works: set) -> dict:
    """阶段1：数据分析，生成详尽的统计报告。"""
    # 统计未注册作品
    detected_works = set()
    for m in scan_result['models']:
        if m['raw_work'] != 'Unknown' and m['raw_work'] != m['work']:
            detected_works.add(m['raw_work'])

    report = {
        'author_count': scan_result['stats']['author_dirs'],
        'model_count': scan_result['stats']['model_folders'],
        'unknown_count': scan_result['stats']['unknown_work'],
        'unregistered_works': sorted(detected_works),
        'unregistered_count': len(detected_works),
        'work_count': len(scan_result['works']),
        'known_work_count': len(known_works),
        'duplicate_groups': 0,
        'duplicate_waste': 0,
        'duplicate_details': [],
        'work_breakdown': [],
        'author_breakdown': [],
    }

    # 作品分布（只显示已定义作品 + Unknown）
    for work, data in sorted(scan_result['works'].items(), key=lambda x: -len(x[1]['models'])):
        if work == 'Unknown' or work in known_works:
            unique_authors = len(data['authors'])
            report['work_breakdown'].append({
                'work': work,
                'model_count': len(data['models']),
                'author_count': unique_authors,
            })

    # 重复分析
    for dedup_key, entries in scan_result['duplicates'].items():
        if len(entries) > 1:
            report['duplicate_groups'] += 1
            report['duplicate_waste'] += len(entries) - 1
            report['duplicate_details'].append({
                'key': dedup_key,
                'count': len(entries),
                'entries': [
                    {'author': e['author_id'], 'folder': e['folder_name'],
                     'files': e['file_count'], 'size': e['total_size']}
                    for e in entries
                ],
            })

    # 作者统计
    author_models = defaultdict(list)
    for m in scan_result['models']:
        author_models[m['author_id']].append(m)
    for aid in sorted(author_models.keys()):
        models = author_models[aid]
        works = set(m['work'] for m in models)
        total_size = sum(m['total_size'] for m in models)
        name = scan_result['authors'].get(aid, {}).get('name', aid)
        report['author_breakdown'].append({
            'author_id': aid,
            'name': name,
            'model_count': len(models),
            'work_count': len(works),
            'total_size_mb': round(total_size / (1024 * 1024), 2),
        })

    return report


def print_report(report: dict) -> None:
    """打印数据分析报告。"""
    print('=' * 60)
    print('  YSM 模型仓库 - 按作品重组 数据分析报告')
    print('=' * 60)
    print(f'\n📊 总体统计')
    print(f'  作者数:            {report["author_count"]}')
    print(f'  模型文件夹:        {report["model_count"]}')
    print(f'  已定义作品数:      {report["known_work_count"]}')
    print(f'  Unknown 模型:      {report["unknown_count"]}')
    print(f'  未注册作品缩写:    {report["unregistered_count"]} 个')
    print(f'  重复组数:          {report["duplicate_groups"]}')
    print(f'  重复浪费:          {report["duplicate_waste"]} 个文件夹')

    print(f'\n📂 作品分布（前20）')
    print(f'  {"作品":<20} {"模型数":>6} {"作者数":>6}')
    print(f'  {"-"*34}')
    for wb in report['work_breakdown'][:20]:
        print(f'  {wb["work"]:<20} {wb["model_count"]:>6} {wb["author_count"]:>6}')

    if report['unregistered_works']:
        print(f'\n⚠️ 未注册作品缩写（共 {report["unregistered_count"]} 个，将归入 _unknown/）')
        # 分组显示
        for w in report['unregistered_works']:
            print(f'  · {w}')

    if report['duplicate_details']:
        print(f'\n🔁 重复详情（前10）')
        for dup in report['duplicate_details'][:10]:
            print(f'  [{dup["key"]}] ×{dup["count"]}')
            for e in dup['entries']:
                size_mb = round(e['size'] / (1024 * 1024), 2)
                print(f'    {e["author"]}: {e["folder"]} ({e["files"]}文件, {size_mb}MB)')

    print(f'\n👤 作者统计（前10）')
    print(f'  {"编号":<6} {"名称":<20} {"模型数":>6} {"作品数":>6} {"总大小":>10}')
    print(f'  {"-"*48}')
    for ab in sorted(report['author_breakdown'], key=lambda x: -x['model_count'])[:10]:
        print(f'  {ab["author_id"]:<6} {ab["name"]:<20} {ab["model_count"]:>6} '
              f'{ab["work_count"]:>6} {ab["total_size_mb"]:>8}MB')

    print()
    print(f'预计迁移后目录结构:')
    print(f'  Models/')
    for wb in report['work_breakdown'][:10]:
        if wb['work'] != 'Unknown':
            print(f'  ├── {wb["work"]}/  ({wb["model_count"]} 个模型)')
    print(f'  ├── ... 共 {report["known_work_count"]} 个已定义作品')
    print(f'  └── _unknown/  ({report["unknown_count"]} 个未分类，含 {report["unregistered_count"]} 个未注册作品)')
    print()


def stage2_create_data_layer(scan_result: dict, known_works: set, apply: bool = False) -> dict:
    """阶段2：创建数据层 work-info/works.json 和 model-info/models.json。"""
    print(f'\n{"="*60}')
    print(f'  阶段2: 创建数据层')
    print(f'  {"(dry-run, 不写盘)" if not apply else "写入磁盘"}')
    print(f'{"="*60}')

    # ---- 2a. 构建 works.json ----
    character_files = sorted(CHARACTER_DIR.glob('*.json')) if CHARACTER_DIR.is_dir() else []
    works_data = {}
    characters_data = {}

    print(f'  从 {len(character_files)} 个 character 文件提取作品信息...')

    for cf in character_files:
        data = load_json_safe(cf)
        if not data or 'work' not in data:
            continue
        work = data['work']
        abbr = work.get('abbr', cf.stem)
        works_data[abbr] = {
            'name': work.get('name', {}),
            'category': work.get('category', 'Other'),
        }
        roles = data.get('roles', [])
        if roles:
            characters_data[abbr] = roles

    # ---- 2b. 构建 models.json ----
    models_data = {}
    for m in scan_result['models']:
        model_key = m['folder_name']
        if model_key in models_data:
            existing = models_data[model_key]
            if m['author_id'] not in existing['authors']:
                existing['authors'].append(m['author_id'])
            existing_files = {f['name'] for f in existing.get('files', [])}
            for f in m['files']:
                if f['name'] not in existing_files:
                    existing.setdefault('files', []).append(f)
                    existing_files.add(f['name'])
            existing['file_count'] = len(existing['files'])
            existing['total_size'] = sum(f['size'] for f in existing.get('files', []))
            continue

        target_dir = '_unknown' if m['work'] == 'Unknown' else m['work']
        models_data[model_key] = {
            'work': m['work'],
            'raw_work': m['raw_work'],
            'en_name': m['en_name'],
            'zh_name': m['zh_name'],
            'rating': m['rating'],
            'authors': [m['author_id']],
            'files': m['files'],
            'file_count': m['file_count'],
            'total_size': m['total_size'],
            'preview_count': m['preview_count'],
            'new_path': f"{target_dir}/{m['new_folder_name']}",
        }

    # 按作品分组统计模型数
    work_model_counts = defaultdict(int)
    for mk, md in models_data.items():
        w = md['work']
        if w == 'Unknown':
            w = '_unknown'
        work_model_counts[w] += 1

    # 写入 works.json
    output = {
        'version': '2.0',
        'generated': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
        'works': works_data,
        'work_model_counts': dict(work_model_counts),
    }
    if apply:
        save_json(WORK_INFO_DIR / 'works.json', output)
        print(f'  ✅ 写入 work-info/works.json ({len(works_data)} 个作品)')
    else:
        print(f'  📄 将写入 work-info/works.json ({len(works_data)} 个作品)')

    # 写入 models.json
    models_output = {
        'version': '2.0',
        'generated': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
        'models': models_data,
    }
    if apply:
        save_json(MODEL_INFO_DIR / 'models.json', models_output)
        print(f'  ✅ 写入 model-info/models.json ({len(models_data)} 个模型)')
    else:
        print(f'  📄 将写入 model-info/models.json ({len(models_data)} 个模型)')

    # 写入 characters.json
    if characters_data:
        chars_output = {
            'version': '2.0',
            'generated': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
            'characters': characters_data,
        }
        if apply:
            save_json(MODEL_INFO_DIR / 'characters.json', chars_output)
            print(f'  ✅ 写入 model-info/characters.json ({sum(len(v) for v in characters_data.values())} 个角色)')
        else:
            print(f'  📄 将写入 model-info/characters.json')

    return models_data


def stage3_migrate_models(scan_result: dict, models_data: dict, apply: bool = False) -> None:
    """阶段3：物理迁移 — 创建 Models/<作品>/ 目录，移动模型文件夹。

    迁移规则：
    - 已定义作品 → Models/<work>/<new_folder_name>/
    - Unknown/未注册 → Models/_unknown/<new_folder_name>/
    - 文件夹名去掉作品缩写前缀（BA_Hoshino_星野_LA → Hoshino_星野_LA）
    - 真正重复（同一文件夹、同一作者）只保留一份
    """
    print(f'\n{"="*60}')
    print(f'  阶段3: 物理迁移模型')
    print(f'  {"(dry-run, 不移动文件)" if not apply else "执行移动"}')
    print(f'{"="*60}')

    # 规划每个模型的目标路径
    target_map = {}  # target_key → {authors, source, target, ...}

    for m in scan_result['models']:
        new_name = m['new_folder_name']

        if m['work'] == 'Unknown':
            target_dir = MODELS_DIR / '_unknown' / new_name
        else:
            target_dir = MODELS_DIR / m['work'] / new_name

        source_path = MODELS_DIR / m['author_id'] / m['folder_name']
        target_key = str(target_dir)

        if target_key not in target_map:
            target_map[target_key] = {
                'authors': [m['author_id']],
                'source': source_path,
                'target': target_dir,
                'file_count': m['file_count'],
                'total_size': m['total_size'],
            }
        else:
            if m['author_id'] not in target_map[target_key]['authors']:
                target_map[target_key]['authors'].append(m['author_id'])

    # 输出迁移计划
    print(f'\n  迁移计划: {len(target_map)} 个目标目录')

    # 按作品分组显示
    by_work = defaultdict(list)
    for tkey, tinfo in target_map.items():
        work = tinfo['target'].parent.name
        by_work[work].append(tinfo)

    for work in sorted(by_work.keys()):
        infos = by_work[work]
        print(f'  [{work}] {len(infos)} 个模型')
        for tinfo in infos[:3]:
            src_rel = str(tinfo['source'].relative_to(REPO_ROOT))
            dst_rel = str(tinfo['target'].relative_to(REPO_ROOT))
            authors_str = ','.join(tinfo['authors'])
            print(f'    {src_rel} → {dst_rel} ({authors_str})')
        if len(infos) > 3:
            print(f'    ... 还有 {len(infos)-3} 个')

    # 执行迁移
    if apply:
        moved_count = 0
        error_count = 0
        for tkey, tinfo in target_map.items():
            source = tinfo['source']
            target = tinfo['target']
            if not source.is_dir():
                print(f'  ⚠️ 源目录不存在: {source}')
                error_count += 1
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target, dirs_exist_ok=True)
                print(f'  ✅ {source.name} → {target}')
                moved_count += 1
            except Exception as e:
                print(f'  ❌ 复制失败: {source.name} → {target}: {e}')
                error_count += 1

        if moved_count > 0:
            print(f'\n  删除源目录...')
            for tkey, tinfo in target_map.items():
                source = tinfo['source']
                if source.is_dir() and target_map[tkey]['target'].is_dir():
                    try:
                        shutil.rmtree(source)
                    except Exception as e:
                        print(f'  ⚠️ 删除失败: {source}: {e}')

            # 删除空的作者目录
            for d in sorted(MODELS_DIR.iterdir()):
                if d.is_dir() and d.name.isdigit() and len(d.name) == 4:
                    remaining = [x for x in d.iterdir() if x.name not in ('README.md', 'info.json')]
                    if not remaining:
                        try:
                            shutil.rmtree(d)
                            print(f'  🗑️ 删除空目录: {d.name}')
                        except Exception as e:
                            print(f'  ⚠️ 删除失败: {d.name}: {e}')

        print(f'\n  迁移完成: {moved_count} 成功, {error_count} 失败')
    else:
        print(f'\n  (dry-run) 未执行任何文件操作')
        print(f'  使用 --apply 执行迁移')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='按作品重组模型目录结构（方案C）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--apply', action='store_true',
                        help='执行写盘操作（默认 dry-run，只预览）')
    parser.add_argument('--stage', type=int, default=0, choices=[0, 1, 2, 3, 4],
                        help='执行阶段: 1=分析, 2=数据层, 3=迁移, 4=清理 (默认全部)')
    args = parser.parse_args()

    print(f'YSM 模型仓库 - 按作品重组 ({"执行" if args.apply else "DRY-RUN 预览"})')
    print(f'仓库根目录: {REPO_ROOT}')
    print()

    # 加载已定义作品
    known_works = load_known_works()
    print(f'已加载 {len(known_works)} 个已定义作品（来自 character/*.json）')
    print()

    # 阶段1：数据分析
    print('阶段1: 扫描并分析当前模型状态...')
    scan_result = scan_models(known_works)
    report = stage1_analyze(scan_result, known_works)
    print_report(report)

    if args.stage and args.stage == 1:
        return 0

    # 阶段2：创建数据层
    if args.stage == 0 or args.stage == 2:
        models_data = stage2_create_data_layer(scan_result, known_works, apply=args.apply)
        if args.stage == 2:
            return 0
    else:
        models_data = load_json_safe(MODEL_INFO_DIR / 'models.json', {})
        if isinstance(models_data, dict):
            models_data = models_data.get('models', {})

    # 阶段3：物理迁移
    if args.stage == 0 or args.stage == 3:
        stage3_migrate_models(scan_result, models_data, apply=args.apply)
        if args.stage == 3:
            return 0

    # 阶段4：清理
    if args.stage == 0 or args.stage == 4:
        print(f'\n{"="*60}')
        print(f'  阶段4: 清理与收尾')
        print(f'{"="*60}')
        print('  需要手动操作的步骤:')
        print('  1. 更新 lib/paths.py 中的目录常量')
        print('  2. 更新 cli.py 中的子命令（添加 restructure）')
        print('  3. 更新 build_site.py 的扫描逻辑')
        print('  4. 重新生成 README（作者索引+作品分类）')
        print()

    return 0


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    raise SystemExit(main())