# -*- coding: utf-8 -*-
"""数据契约校验：用 JSON Schema 校验 .github/data 下各数据文件。

数据架构的"软件化"保障：每份共享数据都有 schema 契约（.github/data/schemas/），
脚本写数据后（或 CI 中）用本模块做结构校验，防止字段漂移/类型错乱。

用法：
  python .github/scripts/lib/validate.py          # 校验全部数据
  或经统一 CLI: python .github/scripts/cli.py check

依赖 jsonschema（pip install jsonschema）；缺失时打印安装提示并退出非零，
避免静默跳过校验（否则契约形同虚设）。
"""
import sys
from pathlib import Path

# 允许作为独立脚本运行（python lib/validate.py）
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths  # noqa: E402

try:
    import jsonschema  # noqa: F401
    from jsonschema import Draft7Validator
except ImportError:
    Draft7Validator = None  # type: ignore[assignment]

# (schema 文件名, 数据相对 .github/data 的路径；character 为目录通配)
# 每个数据文件必须有 schema 契约；co_creators 可能不存在（按需生成），跳过不报错。
CHECKS: list[tuple[str, str]] = [
    ('authors.schema.json', 'author-info/authors.json'),
    ('tag_labels.schema.json', 'author-info/tag_labels.json'),
    ('merge_skips.schema.json', 'model-info/merge_skips.json'),
    ('skin_tags.schema.json', 'model-info/skin_tags.json'),
    ('role_terms.schema.json', 'author-info/role_terms.json'),
    ('maker_rules.schema.json', 'author-info/maker_rules.json'),
    ('platform_map.schema.json', 'author-info/platform_map.json'),
    ('co_creators.schema.json', 'author-info/co_creators.json'),
]
ROLES_GLOB = 'model-info/character'


def load_schema(schema_name: str) -> dict:
    """读取 schemas/ 下的 schema 文件；缺失视为数据契约错误。"""
    path = lib_paths.data_path('schemas', schema_name)
    data = lib_paths.load_json(path, None)
    if data is None:
        raise FileNotFoundError(f'schema 缺失: {lib_paths.get_safe_relpath(path)}')
    return data


def validate_instance(schema: dict, instance, title: str) -> list[str]:
    """校验单个数据实例，返回错误消息列表（空 = 通过）。"""
    if Draft7Validator is None:
        return [f'{title}: jsonschema 未安装，跳过校验（pip install jsonschema）']
    errors = sorted(Draft7Validator(schema).iter_errors(instance),
                    key=lambda e: list(e.path))
    return [f'{title}: {".".join(map(str, e.path)) or "<root>"} {e.message}'
            for e in errors]


def check_all() -> int:
    """校验全部已知数据文件；打印逐项报告，返回退出码（0=全过，1=有错）。"""
    print('== 数据契约校验（schemas/）==')
    failures: list[str] = []

    # 单文件检查
    for schema_name, rel in CHECKS:
        data_path = lib_paths.DATA_DIR / rel
        if not data_path.is_file():
            # co_creators 按需生成，缺失是合法状态；其余文件缺失是问题
            if schema_name == 'co_creators.schema.json':
                print(f'  [跳过] {rel}（不存在，按需生成）')
            else:
                failures.append(f'{rel}: 文件缺失')
            continue
        try:
            schema = load_schema(schema_name)
        except FileNotFoundError as e:
            failures.append(str(e))
            continue
        instance = lib_paths.load_json(data_path, None)
        if instance is None:
            failures.append(f'{rel}: 无法解析')
            continue
        errors = validate_instance(schema, instance, rel)
        if errors:
            failures.extend(errors)
            print(f'  [失败] {rel}')
            for err in errors:
                print(f'    - {err}')
        else:
            print(f'  [通过] {rel}')

    # roles/ 目录通配检查
    roles_dir = lib_paths.DATA_DIR / ROLES_GLOB
    role_files = sorted(roles_dir.glob('*.json')) if roles_dir.is_dir() else []
    if role_files:
        try:
            schema = load_schema('roles.schema.json')
        except FileNotFoundError as e:
            failures.append(str(e))
            role_files = []
        for f in role_files:
            instance = lib_paths.load_json(f, None)
            rel = f'{ROLES_GLOB}/{f.name}'
            if instance is None:
                failures.append(f'{rel}: 无法解析')
                continue
            errors = validate_instance(schema, instance, rel)
            if errors:
                failures.extend(errors)
                print(f'  [失败] {rel}')
                for err in errors:
                    print(f'    - {err}')
            else:
                print(f'  [通过] {rel}')
        print(f'  (roles/ 共 {len(role_files)} 个文件)')
        # 跨文件唯一性：作品 abbr 与 id 必须全局唯一（撞了报错，避免 setdefault/覆盖静默破坏）
        seen_abbr: dict[str, str] = {}
        seen_id: dict[str, str] = {}
        for f in role_files:
            instance = lib_paths.load_json(f, None)
            if not isinstance(instance, dict):
                continue
            work = instance.get('work') or {}
            abbr = str(work.get('abbr') or '').strip().lower()
            wid = str(work.get('id') or '').strip()
            if abbr and abbr in seen_abbr:
                failures.append(f'{f.name}: 缩写 abbr={abbr!r} 与 {seen_abbr[abbr]} 冲突（需改其一）')
            elif abbr:
                seen_abbr[abbr] = f.name
            if wid and wid in seen_id:
                failures.append(f'{f.name}: 编号 id={wid!r} 与 {seen_id[wid]} 冲突')
            elif wid:
                seen_id[wid] = f.name
    else:
        print(f'  [跳过] {ROLES_GLOB}/（无文件）')

    if failures:
        print(f'\n校验失败 {len(failures)} 项:')
        for f in failures:
            print(f'  - {f}')
        return 1
    print('\n全部数据通过 schema 校验')
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    return check_all()


if __name__ == '__main__':
    raise SystemExit(main())
