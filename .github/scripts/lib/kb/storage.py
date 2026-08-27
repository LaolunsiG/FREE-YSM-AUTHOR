# -*- coding: utf-8 -*-
"""kb 知识库多文件存储：load / save / migrate（原 kb_tool.py 的存储部分）。

新结构：character/<作品>.json = {"work": {"abbr": 作品键, name: {lang: 标准名},
aliases: {lang: [别名...]}, category}, "roles": [{zh, en, note}]}——作品键由
work.abbr 决定（不再依赖文件名），角色归属由文件级 work.abbr 决定（角色条目不再存 work 字段）。
作品元数据与角色合并到同一作品文件（不再有独立 works.json）。
兼容旧布局（{作品键: {元数据, roles}}、works.json + 角色数组）与旧单文件
ysm_kb.json、旧 SQLite 库（读取兼容，保存自动迁移为新格式）。"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _safe_name(wk: str) -> str:
    """作品键 -> 安全文件名（Windows 非法字符替换为 _）。"""
    return re.sub(r'[\\/:*?"<>|]', '_', wk or "_")


def dumps_custom(obj, indent: int = 2, level: int = 0) -> str:
    """自定义 JSON 序列化：对象字段逐行缩进，数组按元素类型分排。

    用于 works.json 的显示格式：作品条目内 en/zh/ja/category 等字段各占一行
    （便于浏览），数组值（名称列表）横向单行排列（避免每个名字独占一行导致
    文件过长）。与 save_kb_json 配套，保证脚本写回后格式不回退。

    数组统一多行展开（每个元素一行），与仓库手写 JSON 格式一致，
    保证脚本写回后格式不回退、git diff 干净。
    """
    pad = " " * (indent * level)
    inner = " " * (indent * (level + 1))
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = ["{"]
        for i, (k, v) in enumerate(obj.items()):
            key = json.dumps(k, ensure_ascii=False)
            comma = "," if i < len(obj) - 1 else ""
            lines.append(f"{inner}{key}: {dumps_custom(v, indent, level + 1)}{comma}")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        # 数组统一多行展开（每个元素一行），与仓库手写 JSON 格式一致
        lines = ["["]
        for i, x in enumerate(obj):
            comma = "," if i < len(obj) - 1 else ""
            lines.append(f"{inner}{dumps_custom(x, indent, level + 1)}{comma}")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    return json.dumps(obj, ensure_ascii=False)


def _work_to_memory(work: dict) -> tuple[str, dict]:
    """文件 work 对象 -> (作品键, 内存 meta {en/zh/ja 数组, category})。

    新格式 {abbr, name:{lang: [标准名, 别名...]}, category}：name 值已是数组
    （首项=标准名，其余=别名），直接还原为 en/zh/ja 数组。
    兼容旧格式 {abbr, name:{lang: 字符串}, aliases:{lang: [...]}}。
    """
    if 'abbr' in work:
        abbr = work['abbr']
        name_map = work.get('name') or {}
        aliases_map = work.get('aliases') or {}
        meta: dict = {}
        for lang in ('en', 'zh', 'ja'):
            arr: list[str] = []
            nm = name_map.get(lang) if isinstance(name_map, dict) else None
            if nm:
                if isinstance(nm, list):
                    arr.extend(str(x) for x in nm if str(x))
                else:
                    arr.append(str(nm))
            for a in ((aliases_map.get(lang) or []) if isinstance(aliases_map, dict) else []):
                if a and str(a) not in arr:
                    arr.append(str(a))
            if arr:
                meta[lang] = arr
        if work.get('category') is not None:
            meta['category'] = work['category']
        return str(abbr), meta
    # 旧格式：{name: 作品键, en/zh/ja 数组, category}
    abbr = work.get('name') or ''
    meta = {k: v for k, v in work.items() if k != 'name'}
    return str(abbr), meta


def _work_to_file(abbr: str, meta) -> dict:
    """内存 works 值 -> 新文件 work 对象 {abbr, name, aliases, category}。

    兼容三种旧写法：dict（{en/zh/ja 数组, category}）、list（视为英文名列表）、
    字符串（视为单个英文名）。en/zh/ja 数组首项=标准名，其余=别名。
    """
    if isinstance(meta, dict):
        en = meta.get('en') or []
        zh = meta.get('zh') or []
        ja = meta.get('ja') or []
        category = meta.get('category')
    elif isinstance(meta, list):
        en, zh, ja, category = meta, [], [], None
    elif meta:
        en, zh, ja, category = [str(meta)], [], [], None
    else:
        en, zh, ja, category = [], [], [], None
    work: dict = {'abbr': str(abbr), 'name': {}}
    for lang, arr in (('zh', zh), ('en', en), ('ja', ja)):
        if isinstance(arr, str):
            arr = [arr]
        arr = [str(x) for x in arr if str(x)]
        if not arr:
            continue
        # 新格式：name 值为完整数组（首项=标准名，其余=别名），不再拆 aliases
        work['name'][lang] = arr
    if not work['name']:
        work.pop('name')
    if category:
        work['category'] = category
    return work


def load_kb_json(kb_path: Path) -> dict:
    """读取知识库（作品元数据 + 角色都在 character/<作品>.json）。

    新格式：每个 character/<作品>.json = {"work": {"abbr", name, aliases, category},
    "roles": [...]}，读取后组装成内存结构 {"works": {...}, "roles": [...]}。
    兼容旧格式：{作品键: {元数据, roles}}、works.json（作品表）+
    character/<作品>.json（角色数组）也照常读取；旧单文件 ysm_kb.json 仍可读
    （保存时自动迁移为新格式）。
    """
    empty = {"version": 2, "works": {}, "roles": []}
    if not kb_path.exists():
        return empty
    if kb_path.is_file():
        try:
            data = json.loads(kb_path.read_text(encoding="utf-8"))
            data.setdefault("works", {})
            data.setdefault("roles", [])
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 知识库 JSON 无法解析: {e}", file=sys.stderr)
            return empty
    data = {"version": 2, "works": {}, "roles": []}
    old_single = kb_path / "ysm_kb.json"
    if not (kb_path / "character").exists() and old_single.exists():
        # 尚未迁移：读旧单文件（避免手工条目丢失）
        try:
            d = json.loads(old_single.read_text(encoding="utf-8"))
            d.setdefault("works", {})
            d.setdefault("roles", [])
            return d
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 知识库 JSON 无法解析: {e}", file=sys.stderr)
            return empty
    # 新格式：遍历 character/<作品>.json，每个文件 = {作品键: {元数据, roles}}
    rdir = kb_path / "character"
    works: dict = {}
    roles: list = []
    if rdir.is_dir():
        for f in sorted(rdir.glob("*.json")):
            try:
                content = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"[warn] 忽略损坏文件 {f}: {e}", file=sys.stderr)
                continue
            if isinstance(content, dict) and isinstance(content.get("work"), dict):
                # 新格式：{work: {abbr, name, aliases, category}, roles: [...]}
                # 作品键由 work.abbr 决定，不再依赖文件名（文件名仅作人读友好）
                wk, meta = _work_to_memory(content["work"])
                wk = wk or f.stem
                if any(meta.get(fd) for fd in ("en", "zh", "ja", "category")):
                    works[wk] = meta
                for r in (content.get("roles") or []):
                    if isinstance(r, dict):
                        r = dict(r)
                        r.setdefault("work", wk)
                        roles.append(r)
            elif isinstance(content, dict):
                # 旧格式：顶层即 作品键 -> 条目（含 en/cn/ja/category + roles）
                for key, entry in content.items():
                    if not isinstance(entry, dict):
                        continue
                    entry = dict(entry)
                    wk_roles = entry.pop("roles", None) or []
                    # 仅当含作品元数据（en/cn/ja/category 之一）才算作品键；
                    # 纯角色文件（别名 work，如 character/ATRI.json 无元数据）不产生空作品键
                    if any(entry.get(f) for f in ("en", "zh", "ja", "category")):
                        works[key] = entry
                    for r in wk_roles:
                        if isinstance(r, dict):
                            r = dict(r)
                            r.setdefault("work", key)
                            roles.append(r)
            elif isinstance(content, list):
                # 旧格式：该文件是角色数组（作品键 = 文件名）
                key = f.stem
                for r in content:
                    if isinstance(r, dict):
                        r = dict(r)
                        r.setdefault("work", key)
                        roles.append(r)
    # 兼容旧 works.json（合并前若有独立作品表，并入 works）
    wf = kb_path / "works.json"
    if wf.exists():
        try:
            legacy = json.loads(wf.read_text(encoding="utf-8"))
            if isinstance(legacy, dict):
                for k, v in legacy.items():
                    works.setdefault(k, v)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 忽略损坏文件 {wf}: {e}", file=sys.stderr)
    data["works"] = works
    data["roles"] = roles
    return data


def save_kb_json(kb_path: Path, data: dict) -> None:
    """写回知识库（新格式）：作品元数据 + 角色合并进 character/<作品>.json。

    新格式：每个 character/<作品>.json = {"work": {"abbr", name, aliases, category},
    "roles": [{cn, en, note}]}——作品键由 work.abbr 决定，角色条目不存 work。
    不再写独立 works.json。
    kb_path 为目录；若为旧单文件路径则以其父目录为数据根并迁移。
    """
    if kb_path.is_file():
        old_single = kb_path
        kb_path = old_single.parent
    else:
        old_single = kb_path / "ysm_kb.json"
    kb_path.mkdir(parents=True, exist_ok=True)
    data["version"] = 2
    # works 值支持三种写法：平铺数组 / 空 [] / 按语言分类对象
    works_out: dict = {}
    for k, v in (data.get("works") or {}).items():
        if isinstance(v, dict):
            d_out: dict = {}
            for lang, names in v.items():
                if lang == "category":
                    # category 为标量或数组大类（str 或 [str,...]），原样保留（去空）
                    d_out[lang] = ([x for x in names if x]
                                   if isinstance(names, list) else names)
                    continue
                if isinstance(names, list):
                    out = []
                    for x in names:
                        if x and x not in out:
                            out.append(x)
                    d_out[lang] = out
                elif names:
                    d_out[lang] = [names]
                else:
                    d_out[lang] = []
            works_out[k] = d_out
        elif isinstance(v, list):
            out = []
            for x in v:
                if x and x not in out:
                    out.append(x)
            works_out[k] = out
        elif v:
            works_out[k] = [v]
        else:
            works_out[k] = []
    data["works"] = dict(sorted(works_out.items()))
    # 角色 work 键归一化：角色可能用作品别名（如 work=ATRI/NGO）而非规范键
    # （AIRI/NEO），合并时统一归到规范作品键，避免同一作品分散在多个文件。
    _alias_to_key: dict[str, str] = {}
    for key, entry in data["works"].items():
        if isinstance(entry, dict):
            for field in ("en", "zh", "ja"):
                names = entry.get(field) or []
                if isinstance(names, str):
                    names = [names]
                for n in names:
                    if n:
                        _alias_to_key.setdefault(str(n).lower(), key)
        elif isinstance(entry, list):
            for n in entry:
                if n:
                    _alias_to_key.setdefault(str(n).lower(), key)
        _alias_to_key.setdefault(str(key).lower(), key)
    for r in (data.get("roles") or []):
        for f in ("zh", "en"):
            v = r.get(f)
            if isinstance(v, list):
                out = []
                for x in v:
                    if x and x not in out:
                        out.append(x)
                r[f] = out
        wk = str(r.get("work", ""))
        canon = _alias_to_key.get(wk.lower())
        if canon:
            r["work"] = canon
    roles = sorted(data.get("roles") or [],
                   key=lambda r: (str(r.get("work", "")), str(r.get("zh", ""))))
    # roles 按作品分组写 character/<作品>.json（重建前清空旧文件）
    rdir = kb_path / "character"
    rdir.mkdir(exist_ok=True)
    for f in rdir.glob("*.json"):
        f.unlink()
    groups: dict[str, list] = {}
    for r in roles:
        groups.setdefault(str(r.get("work", "_")), []).append(r)
    # 作品文件 = {"work": {abbr, name, aliases, category}, "roles": [...]}；没有角色的作品也保留文件。
    # 键由 work.abbr 决定（读取不依赖文件名），文件名仅作人读友好。
    all_keys = set(data["works"]) | set(groups)
    seen_files: dict[str, str] = {}
    for wk in sorted(all_keys):
        fname = _safe_name(wk)
        prev = seen_files.get(fname)
        if prev and prev != wk:
            # 两个作品键转义后撞同名文件：读取靠 work.name 不依赖文件名，
            # 但会互相覆盖文件内容，必须显式报错而非静默丢数据。
            raise ValueError(
                f"作品键 {prev!r} 与 {wk!r} 转义后同为文件 {fname!r}，"
                f"请调整其中一个作品键以避免覆盖")
        seen_files[fname] = wk
        meta = data["works"].get(wk) or {}
        work_obj = _work_to_file(wk, meta)
        # 角色条目剥离 work 字段（归属由文件级 work.name 决定，避免冗余与不一致）
        wk_roles = [{k: v for k, v in r.items() if k != "work"}
                    for r in groups.get(wk, [])]
        (rdir / f"{fname}.json").write_text(
            dumps_custom({"work": work_obj, "roles": wk_roles}) + "\n",
            encoding="utf-8")
    # 删除已并入的 works.json（合并完成）
    wf = kb_path / "works.json"
    if wf.exists():
        wf.unlink()
        print(f"已合并 works.json 进 character/*.json，旧 {wf.name} 已删除")
    if old_single and old_single.exists():
        print(f"已迁移为合并格式（character/*.json = 作品元数据+角色），"
              f"旧 {old_single.name} 可删除")


def migrate_works_into_character(kb_path: Path) -> None:
    """把独立 works.json 合并进 character/<作品>.json（新格式），删除 works.json。

    兼容旧布局：works.json（作品表）+ character/<作品>.json（角色数组）
    -> 新布局：character/<作品>.json = {作品键: {元数据, roles:[...]}}。
    通过 load 内存结构 + save 新格式完成，幂等（已合并则无 works.json 可删）。
    """
    data = load_kb_json(kb_path)
    save_kb_json(kb_path, data)
    print(f"已合并作品元数据 + 角色 -> character/*.json（{len(data['works'])} 个作品）")


def migrate_from_sqlite(kb_path: Path, sqlite_path: Path) -> tuple[list[dict], list[dict]]:
    """首次迁移：旧 SQLite 库存在且 JSON 不存在时，搬移手工角色与别名。"""
    if kb_path.exists() or not sqlite_path.exists():
        return [], []
    try:
        conn = sqlite3.connect(str(sqlite_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT cn, en, work, note FROM roles WHERE source = 'manual'")
            manual = [{"zh": r[0], "en": r[1], "work": r[2],
                       "note": r[3] or ""} for r in cur.fetchall()]
            cur.execute("SELECT kind, alias, canonical, work, note FROM aliases")
            aliases = [{"kind": r[0], "alias": r[1], "canonical": r[2],
                        "work": r[3], "note": r[4] or ""}
                       for r in cur.fetchall()]
        finally:
            conn.close()
        print(f"已从旧 SQLite 迁移 {len(manual)} 条手工角色、{len(aliases)} 条别名")
        return manual, aliases
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 旧 SQLite 迁移失败（忽略）: {e}", file=sys.stderr)
        return [], []

# 在 lib/kb/storage.py 末尾添加

def add_role_to_work(role_name: str, work_key: str, kb_path: Path,
                     data: dict | None = None) -> bool:
    """
    将角色名（不格式化）添加到指定作品 JSON 的 roles 列表中。
    如果 data 为 None，则重新加载整个知识库。
    返回 True 表示新增成功，False 表示该角色已存在。
    """
    char_dir = kb_path / "character" if (kb_path / "character").is_dir() else kb_path
    char_file = char_dir / f"{work_key}.json"
    if data is None:
        data = load_kb_json(kb_path)  # 临时加载
    char_data = load_json(char_file, {"work": {}, "roles": []})

    # 如果 work 信息缺失，从 data 中补全
    if "work" not in char_data or not char_data["work"]:
        wk_info = (data.get("works") or {}).get(work_key, {})
        char_data["work"] = {
            "abbr": work_key,
            "name": {"zh": (wk_info.get("zh") or [""])[:1] if wk_info.get("zh") else [],
                     "en": (wk_info.get("en") or [""])[:1] if wk_info.get("en") else []},
            "category": wk_info.get("category", "Other"),
        }

    # 检查是否已存在（精确匹配中英文）
    for role in char_data.get("roles", []):
        if role_name in (role.get("zh") or []) or role_name in (role.get("en") or []):
            return False

    char_data.setdefault("roles", []).append({"zh": [role_name]})
    save_json(char_file, char_data)
    return True