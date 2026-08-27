# -*- coding: utf-8 -*-
"""kb 交互命令 / 检查 / 合并 / 索引 / 扫描（原 kb_tool.py 的命令与工具部分）。

依赖分层：text -> parse -> storage / sync -> cmds（本模块为最上层）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lib import console as lib_console  # noqa: E402
from lib import paths as lib_paths  # noqa: E402
from lib.kb import parse, storage, sync  # noqa: E402
from lib.kb.category import (  # noqa: E402
    CATEGORIES, build_category_map, update_readme_works_section,
)
from lib.kb.parse2 import resolve_name3  # noqa: E402
from lib.kb.text import normalize_en_key  # noqa: E402

REPO_ROOT = lib_paths.WORKSPACE_ROOT
DEFAULT_ROOTS = [REPO_ROOT / "Models", REPO_ROOT / "Other-YSM-Models"]

# storage/sync 的常用别名（本模块内使用）
load_kb_json = storage.load_kb_json
save_kb_json = storage.save_kb_json
migrate_from_sqlite = storage.migrate_from_sqlite
build_work_index = sync.build_work_index
role_key = parse.role_key
role_names = parse.role_names


def ask(prompt: str) -> str:
    """安全的交互输入（复用 lib/console.py 统一实现；别名保留以兼容外部引用）。

    返回 'q' 后各交互命令会保存已完成的部分并优雅退出（与显式输入 q 等价），
    避免用户在确认环节按 Ctrl+C 时直接抛 KeyboardInterrupt 崩溃。
    """
    return lib_console.ask(prompt)


def add_manual_entries(kb_path: Path) -> None:
    """交互式添加角色条目（纯手工维护，无自动构建）。"""
    data = load_kb_json(kb_path)
    print("交互式添加对照条目：逐项输入，回车跳过；输入 q 结束。")
    print("提示：英文名请填文件夹中出现的写法（如 kitasan_black），")
    print("      或规范形式（kitasan-black）——两者都能匹配。")
    added = 0
    while True:
        print("-" * 40)
        cn = ask("中文角色名 (可空): ")
        if cn.lower() in ("q", "quit"):
            break
        en = ask("英文角色名 (可空): ")
        if en.lower() in ("q", "quit"):
            break
        work = ask("作品 (必填，如 BA/AK/OC): ")
        if work.lower() in ("q", "quit"):
            break
        if not work:
            print("作品不能为空，本条跳过。")
            continue
        if not cn and not en:
            print("中文名和英文名至少填一个，本条跳过。")
            continue
        note = ask("备注 (可空): ")
        if note.lower() in ("q", "quit"):
            break
        data["roles"].append({"zh": cn or "", "en": en or "", "work": work,
                              "note": note})
        save_kb_json(kb_path, data)
        added += 1
        print(f"已添加: {cn or '?'} | {en or '?'} | {work}")
    print(f"共添加 {added} 条。知识库: {kb_path}")


def list_db(kb_path: Path) -> None:
    """列出知识库全部条目（角色对照）。"""
    data = load_kb_json(kb_path)
    roles = data.get("roles") or []
    if not roles:
        print(f"知识库为空或不存在: {kb_path}（请用 --roles / --add 添加角色）")
        return
    print(f"角色对照 {len(roles)} 条（纯手工维护）:")
    for r in roles:
        cn_v, en_v = r.get("zh"), r.get("en")
        cn_s = cn_v[0] if isinstance(cn_v, list) else (cn_v or '-')
        en_s = en_v[0] if isinstance(en_v, list) else (en_v or '-')
        extra = ""
        if isinstance(cn_v, list) and len(cn_v) > 1:
            extra += f" (+{len(cn_v) - 1}中文别名)"
        if isinstance(en_v, list) and len(en_v) > 1:
            extra += f" (+{len(en_v) - 1}英文别名)"
        line = (f"  {r.get('work', ''):<12} | {cn_s:<14} | {en_s:<28}{extra}")
        if r.get("note"):
            line += f"  #{r['note']}"
        print(line)


def del_entries(kb_path: Path) -> None:
    """交互式删除条目（角色对照或别名）。按关键词搜索 -> 选编号删除。"""
    data = load_kb_json(kb_path)
    while True:
        kw = ask("搜索关键词 (中文名/英文名/作品，留空=列出全部，q=退出): ")
        if kw.lower() in ("q", "quit"):
            break
        print("-" * 60)
        roles = data.get("roles") or []
        if kw:
            r_hits = [r for r in roles
                      if kw.lower() in str(r.get("zh", "")).lower()
                      or kw.lower() in str(r.get("en", "")).lower()
                      or kw.lower() in str(r.get("work", "")).lower()]
        else:
            r_hits = roles
        if r_hits:
            print(f"角色对照 {len(r_hits)} 条（输入编号删除，多个用逗号分隔）:")
            for i, r in enumerate(r_hits, 1):
                line = (f"  [{i}] {r.get('work', ''):<12} | {r.get('zh') or '-':<14}"
                        f" | {r.get('en') or '-':<28}")
                if r.get("note"):
                    line += f"  #{r['note']}"
                print(line)
        if not r_hits:
            print("无匹配条目。")
            continue
        sel = ask("要删除的编号（多个用逗号分隔；留空=不删，q=退出）: ")
        if sel.lower() in ("q", "quit"):
            break
        if not sel:
            continue
        removed = 0
        for token in sel.replace("，", ",").split(","):
            token = token.strip()
            if not token:
                continue
            idx = int(token) - 1
            if 0 <= idx < len(r_hits):
                roles.remove(r_hits[idx])
                removed += 1
        data["roles"] = roles
        save_kb_json(kb_path, data)
        print(f"已删除 {removed} 条。")


# ---------------------------------------------------------------------------
# 统一交互式角色管理（--roles）：增删改查 + 别名（推荐入口）。
# ---------------------------------------------------------------------------






def _role_line(i: int, r: dict) -> str:
    """单条角色格式化显示（供增删改查复用）。"""
    cn_s = " / ".join(r.get("zh") or []) or "-"
    en_s = " / ".join(r.get("en") or []) or "-"
    line = f"  [{i}] {str(r.get('work', '')):<10} | cn: {cn_s} | en: {en_s}"
    if r.get("note"):
        line += f"  #{r['note']}"
    return line


def _ask_list(prompt: str) -> list[str] | None:
    """输入逗号分隔的多值 -> 列表；空返回 []；q 返回 None。"""
    s = ask(prompt).strip()
    if s.lower() in ("q", "quit"):
        return None
    return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]


def _role_pick(data: dict, prompt: str) -> dict | None:
    """搜索角色 -> 返回选中的条目（供编辑/设默认复用）。"""
    roles = data.get("roles") or []
    while True:
        kw = ask(prompt).strip()
        if kw.lower() in ("q", "quit"):
            return None
        hits = [r for r in roles
                if kw in str(r.get("zh", "")) or kw in str(r.get("en", ""))
                or kw in str(r.get("work", ""))]
        if not hits:
            print("无匹配条目，换个关键词。")
            continue
        print(f"命中 {len(hits)} 条：")
        for i, r in enumerate(hits, 1):
            print(_role_line(i, r))
        sel = ask("选择编号（Enter=重新搜索, q=取消）: ").strip()
        if sel.lower() in ("q", "quit"):
            return None
        if sel.isdigit() and 1 <= int(sel) <= len(hits):
            return hits[int(sel) - 1]
        print("编号无效，重新搜索。")


def _role_add(data: dict, work: str | None = None) -> str | None:
    """添加角色（cn/en/ja 多个用逗号分隔，第一个为规范名）。

    work 非空时跳过作品选择，直接在该作品下添加；返回 work 键（供循环继续），None 表示取消。
    """
    print("添加角色（q=返回上一级）")
    # 作品查找与选择（work 已指定时跳过）
    if work is None:
        while True:
            raw = ask("作品 (输入名称/键，如 BA/碧蓝档案/蔚蓝档案/Blue-Archive; q=返回): ").strip()
            if raw.lower() in ("q", "quit"):
                return None
            if not raw:
                print("作品不能为空。")
                continue
            works = data.get("works") or {}
            rl = raw.lower()
            # 先精确匹配，再子串匹配
            exact_matches: list[str] = []
            for wk, wv in works.items():
                if not isinstance(wv, dict):
                    continue
                names = [wk]
                for k in ("en", "zh", "ja"):
                    lst = wv.get(k)
                    if isinstance(lst, list):
                        names.extend(str(x) for x in lst)
                for n in names:
                    if rl == n.lower():
                        exact_matches.append(wk)
                        break
            if len(exact_matches) == 1:
                work = exact_matches[0]
                break
            if len(exact_matches) > 1:
                print(f"找到多个精确匹配 ({len(exact_matches)} 个):")
                for i, wk in enumerate(exact_matches, 1):
                    wv = works.get(wk, {})
                    en = (wv.get("en") or ["-"])[0] if isinstance(wv.get("en"), list) else "-"
                    zh = (wv.get("zh") or ["-"])[0] if isinstance(wv.get("zh"), list) else "-"
                    print(f"  [{i}] {wk} | {zh} | {en}")
                sel = ask("请选择编号 (Enter=取消): ").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(exact_matches):
                    work = exact_matches[int(sel) - 1]
                    break
                print("未选择，重新输入。")
                continue
            # 精确匹配无结果：子串匹配（大小写不敏感）
            substr_matches: list[str] = []
            for wk, wv in works.items():
                if not isinstance(wv, dict):
                    continue
                haystack = wk.lower()
                for k in ("en", "zh", "ja"):
                    lst = wv.get(k)
                    if isinstance(lst, list):
                        haystack += " " + " ".join(str(x) for x in lst)
                if rl in haystack:
                    substr_matches.append(wk)
            if len(substr_matches) == 1:
                work = substr_matches[0]
                print(f"  匹配到作品: {work}")
                break
            if len(substr_matches) > 1:
                print(f"找到多个匹配 ({len(substr_matches)} 个):")
                for i, wk in enumerate(substr_matches, 1):
                    wv = works.get(wk, {})
                    en = (wv.get("en") or ["-"])[0] if isinstance(wv.get("en"), list) else "-"
                    zh = (wv.get("zh") or ["-"])[0] if isinstance(wv.get("zh"), list) else "-"
                    print(f"  [{i}] {wk} | {zh} | {en}")
                sel = ask("请选择编号 (Enter=取消): ").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(substr_matches):
                    work = substr_matches[int(sel) - 1]
                    break
                print("未选择，重新输入。")
                continue
            else:
                ans = ask(f"数据库中未找到「{raw}」，是否创建新作品？(y/n): ").strip().lower()
                if ans in ("y", "yes"):
                    work = ask("新作品键 (如 BA/AK/OC): ").strip()
                    if not work or work.lower() in ("q", "quit"):
                        print("取消创建。")
                        continue
                    cn_name = ask("中文全称 (可空): ").strip()
                    en_name = ask("英文全称 (可空): ").strip()
                    entry: dict = {"en": [work], "category": "Game"}
                    if cn_name:
                        entry["zh"] = [cn_name]
                    if en_name:
                        entry["en"] = [en_name]
                    works[work] = entry
                    data["works"] = works
                    break
                print("重新输入。")
                continue

    cn = _ask_list("中文名 (逗号分隔，可空): ")
    if cn is None:
        return None
    en = _ask_list("英文名 (逗号分隔，可空; 第一个为规范名): ")
    if en is None:
        return None
    ja = _ask_list("日文名 (逗号分隔，可空): ")
    if ja is None:
        return None
    if not cn and not en and not ja:
        print("中文名、英文名和日文名至少填一个。")
        return None
    note = ask("备注 (可空): ").strip()
    if note.lower() in ("q", "quit"):
        return None
    entry: dict = {"work": work}
    if cn:
        entry["zh"] = cn
    if en:
        entry["en"] = en
    if ja:
        entry["ja"] = ja
    if note:
        entry["note"] = note
    data.setdefault("roles", []).append(entry)
    print(f"已添加: {work} | cn={cn or '-'} | en={en or '-'} | ja={ja or '-'}")
    return work


def _role_delete(data: dict) -> None:
    """删除角色（搜索 -> 选编号，多个逗号分隔）。"""
    roles = data.get("roles") or []
    while True:
        kw = ask("搜索要删除的角色 (留空=列出全部, q=返回): ").strip()
        if kw.lower() in ("q", "quit"):
            return
        hits = [r for r in roles
                if (not kw) or kw in str(r.get("zh", "")) or kw in str(r.get("en", ""))
                or kw in str(r.get("work", ""))]
        if not hits:
            print("无匹配条目。")
            continue
        print(f"角色 {len(hits)} 条（输入编号删除，多个逗号分隔）：")
        for i, r in enumerate(hits, 1):
            print(_role_line(i, r))
        sel = ask("要删除的编号 (留空=不删, q=返回): ").strip()
        if sel.lower() in ("q", "quit"):
            return
        if not sel:
            continue
        removed = 0
        for token in sel.replace("，", ",").split(","):
            token = token.strip()
            if not token.isdigit():
                continue
            idx = int(token) - 1
            if 0 <= idx < len(hits):
                roles.remove(hits[idx])
                removed += 1
        print(f"已删除 {removed} 条。")


def _edit_names(r: dict, field: str, label: str) -> None:
    """编辑某字段的名称列表（别名增删改；规范名=首项）。

    - a：添加别名（默认追加到列表末尾，不动规范名）；
    - d：删除指定编号（首项规范名不可删）；
    - m：修改指定编号（首项即改规范名）；
    - s：设为默认名（把选中项移到首位）；
    - r：手动重新排序（输入新顺序编号，逗号分隔，如 3,1,2）。
    """
    names = list(r.get(field) or [])
    while True:
        print(f"当前{label}：")
        for i, n in enumerate(names, 1):
            mark = "（规范名）" if i == 1 else ""
            print(f"  [{i}] {n}{mark}")
        print("  a=添加  d=删除  m=修改  s=设为默认  r=排序  0=返回")
        sel = ask("操作: ").strip().lower()
        if sel in ("0", "q", "quit"):
            break
        if sel == "a":
            raw = ask(f"新增{label}（逗号分隔可多个，追加到末尾）: ").strip()
            if raw.lower() in ("q", "quit"):
                continue
            for x in [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]:
                if x not in names:
                    names.append(x)
                    print(f"  已添加别名: {x}")
        elif sel == "s":
            # 设为默认名：选中项移到首位（其余保持原相对顺序）
            idx = ask("编号（设为默认名，移到首位）: ").strip()
            if idx.lower() in ("q", "quit"):
                continue
            nums = [int(t) for t in idx.replace("，", ",").split(",")
                    if t.strip().isdigit()]
            if not nums or not (1 <= nums[0] <= len(names)):
                print("编号无效。")
                continue
            n = nums[0]
            chosen = names.pop(n - 1)
            names.insert(0, chosen)
            print(f"  已设为默认名: {chosen}")
        elif sel == "r":
            # 手动重新排序：输入新顺序编号（如 3,1,2），须一一对应
            for i, n in enumerate(names, 1):
                mark = "（规范名）" if i == 1 else ""
                print(f"  [{i}] {n}{mark}")
            raw = ask("新顺序编号（逗号分隔，必须包含全部编号）: ").strip()
            if raw.lower() in ("q", "quit"):
                continue
            nums = [int(t) for t in raw.replace("，", ",").split(",")
                    if t.strip().isdigit()]
            if sorted(nums) != list(range(1, len(names) + 1)):
                print(f"无效顺序：需包含 1-{len(names)} 的全部编号。")
                continue
            names = [names[i - 1] for i in nums]
            print("  已重新排序。")
        elif sel in ("d", "m"):
            # 输入编号前重新列出编号与名称，方便对照选择
            for i, n in enumerate(names, 1):
                mark = "（规范名）" if i == 1 else ""
                print(f"  [{i}] {n}{mark}")
            idx = ask("编号（逗号分隔可多个）: ").strip()
            if idx.lower() in ("q", "quit"):
                continue
            nums = [int(t) for t in idx.replace("，", ",").split(",")
                    if t.strip().isdigit()]
            if not nums:
                print("编号无效。")
                continue
            if sel == "d":
                if 1 in nums:
                    print("首项是规范名，不可删除（可用 m 修改）。")
                    nums = [n for n in nums if n != 1]
                for n in sorted(set(nums), reverse=True):
                    if 1 <= n <= len(names):
                        print(f"  已删除: {names[n - 1]}")
                        names.pop(n - 1)
                if not names:
                    print("警告：该字段名称已清空。")
            else:  # m
                for n in sorted(set(nums)):
                    if 1 <= n <= len(names):
                        new = ask(f"  修改 [{n}] {names[n - 1]} -> 新值: ").strip()
                        if new.lower() in ("q", "quit"):
                            print("已取消修改。")
                            break
                        if new and new != names[n - 1] and new not in names:
                            names[n - 1] = new
                            print(f"  已修改为: {new}")
                        elif new in names:
                            print(f"  '{new}' 已存在，跳过。")
    r[field] = names


def _role_edit(data: dict) -> None:
    """编辑角色：作品键 / 中英文名（别名增删改） / 备注。"""
    r = _role_pick(data, "搜索要编辑的角色 (q=返回): ")
    if r is None:
        return
    while True:
        print("-" * 56)
        print("编辑角色：")
        print(_role_line(0, r))
        print("  1) 修改作品键   2) 中文名(别名)   3) 英文名(别名)")
        print("  4) 备注          0) 返回")
        sel = ask("选择: ").strip()
        if sel in ("0", "q", "quit"):
            return
        if sel == "1":
            work = ask(f"作品键 [{r.get('work', '')}]: ").strip()
            if work.lower() in ("q", "quit"):
                continue
            if work:
                r["work"] = work
                print(f"  作品键已改为: {work}")
        elif sel == "2":
            _edit_names(r, "zh", "中文名")
        elif sel == "3":
            _edit_names(r, "en", "英文名")

        elif sel == "4":
            note = ask(f"备注 [{(r.get('note') or '')}]: ").strip()
            if note.lower() in ("q", "quit"):
                continue
            if note:
                r["note"] = note
                print("  备注已更新。")
        else:
            print("无效选择。")


def _role_search(data: dict) -> None:
    """搜索/列出角色。"""
    roles = data.get("roles") or []
    kw = ask("搜索关键词 (留空=列出全部, q=返回): ").strip()
    if kw.lower() in ("q", "quit"):
        return
    hits = [r for r in roles
            if (not kw) or kw in str(r.get("zh", "")) or kw in str(r.get("en", ""))
            or kw in str(r.get("work", ""))]
    if not hits:
        print("无匹配条目。")
        return
    print(f"角色 {len(hits)} 条：")
    for i, r in enumerate(hits, 1):
        print(_role_line(i, r))


def _role_set_default(data: dict) -> None:
    """设定角色默认中英文名（写入 cn/en 数组首项）。"""
    r = _role_pick(data, "搜索角色 (q=返回): ")
    if r is None:
        return
    print("当前：")
    print(_role_line(0, r))
    new_cn = ask(f"默认中文名 (写入 cn 首项; 当前 {' / '.join(r.get('zh') or []) or '-'}): ").strip()
    if new_cn.lower() in ("q", "quit"):
        return
    new_en = ask(f"默认英文名 (写入 en 首项; 当前 {' / '.join(r.get('en') or []) or '-'}): ").strip()
    if new_en.lower() in ("q", "quit"):
        return
    if not new_cn and not new_en:
        print("中文名和英文名至少填一个。")
        return
    cn_list = list(r.get("zh") or [])
    en_list = list(r.get("en") or [])
    if new_cn:
        cn_list = [new_cn] + [c for c in cn_list if c != new_cn]
    if new_en:
        en_list = [new_en] + [e for e in en_list if e != new_en]
    r["zh"] = cn_list
    r["en"] = en_list
    print("已设定默认名（写入数组首项，退出时统一保存）。")


def _snapshot(data: dict) -> str:
    """数据快照（排序序列化），用于判断是否有实际改动。"""
    return json.dumps(
        {"roles": data.get("roles")},
        ensure_ascii=False, sort_keys=True)


def _save_if_changed(kb_path: Path, data: dict, before: str) -> None:
    """仅当数据有改动时写回，避免无操作时重写全部文件。"""
    if _snapshot(data) != before:
        save_kb_json(kb_path, data)
        print(f"已保存知识库: {kb_path}")
    else:
        print("无改动，未保存。")


def roles_cmd(kb_path: Path) -> int:
    """交互式角色管理：增删改查 + 别名。"""
    data = load_kb_json(kb_path)
    data.setdefault("roles", [])
    before = _snapshot(data)
    while True:
        print("-" * 56)
        print("角色管理（纯手工维护，有改动才保存）:")
        print("  1) 添加角色    2) 删除角色    3) 合并角色")
        print("  4) 编辑角色    5) 搜索/查看   6) 设定默认名")
        print("  0) 返回")
        sel = ask("选择: ").strip()
        if sel in ("q", "quit", "0"):
            break
        if sel == "1":
            _role_add(data)
        elif sel == "2":
            _role_delete(data)
        elif sel == "3":
            # 合并独立 load/save：先落盘菜单内未保存改动，合并后重载保持内存同步
            _save_if_changed(kb_path, data, before)
            run_merge(kb_path)
            data = load_kb_json(kb_path)
            data.setdefault("roles", [])
            before = _snapshot(data)
        elif sel == "4":
            _role_edit(data)
        elif sel == "5":
            _role_search(data)
        elif sel == "6":
            _role_set_default(data)
        else:
            print("无效选择。")
    _save_if_changed(kb_path, data, before)
    return 0


def add_role_cmd(kb_path: Path) -> int:
    """添加角色，支持连续添加和 Ctrl+C 安全退出。"""
    data = load_kb_json(kb_path)
    data.setdefault("roles", [])
    before = _snapshot(data)
    work: str | None = None
    while True:
        try:
            result = _role_add(data, work)
        except KeyboardInterrupt:
            print("\n已取消，保存已添加的角色。")
            break
        if result is None:
            # 用户取消（q/退出）
            break
        work = result  # 记住当前作品，下次继续在该作品下添加
        ans = ask("回车继续添加角色（当前作品），或输入 q 退出: ").strip().lower()
        if ans in ("q", "quit"):
            break
        # 回车继续，work 已记住，跳过作品选择
    _save_if_changed(kb_path, data, before)
    return 0


def del_role_cmd(kb_path: Path) -> int:
    """单次删除角色（搜索 -> 选编号），复用 _role_delete。"""
    data = load_kb_json(kb_path)
    before = _snapshot(data)
    _role_delete(data)
    _save_if_changed(kb_path, data, before)
    return 0


def list_role_cmd(kb_path: Path) -> int:
    """列出全部角色（新格式显示，含别名）。"""
    data = load_kb_json(kb_path)
    roles = data.get("roles") or []
    if not roles:
        print("知识库暂无角色。")
        return 0
    print(f"角色 {len(roles)} 条：")
    for i, r in enumerate(roles, 1):
        print(_role_line(i, r))
    return 0


def run_check(kb_path: Path) -> None:
    """数据质量检查：同名多作品、空字段、重复条目、别名悬空。"""
    data = load_kb_json(kb_path)
    roles = data.get("roles") or []
    issues: list[str] = []

    def split(v):
        return v if isinstance(v, list) else ([v] if v else [])

    cn_works: dict[str, set] = {}
    en_works: dict[str, set] = {}
    seen_pairs: dict[str, int] = {}
    for r in roles:
        cn_list = [c for c in split(r.get("zh")) if c]
        en_list = [e for e in split(r.get("en")) if e]
        for c in cn_list:
            cn_works.setdefault(c, set()).add(r.get("work", "?"))
        for e in en_list:
            en_works.setdefault(normalize_en_key(e), set()).add(r.get("work", "?"))
        # 完全重复检测（同 cn+en+work）
        key = "|".join([r.get("work", "?"), "&".join(cn_list), "&".join(en_list)])
        seen_pairs[key] = seen_pairs.get(key, 0) + 1
    for c, ws in sorted(cn_works.items()):
        if len(ws) > 1:
            issues.append(f"同名多作品: {c} -> {', '.join(sorted(ws))}")
    for e, ws in sorted(en_works.items()):
        if len(ws) > 1:
            issues.append(f"同英文名多作品: {e} -> {', '.join(sorted(ws))}")
    for r in roles:
        cn_list = [c for c in split(r.get("zh")) if c]
        en_list = [e for e in split(r.get("en")) if e]
        if not cn_list and not en_list:
            issues.append(f"空条目: {r}")
        elif not cn_list or not en_list:
            issues.append(f"缺中/英文名: work={r.get('work', '?')} cn={r.get('zh') or '-'} en={r.get('en') or '-'}")
    for key, cnt in seen_pairs.items():
        if cnt > 1:
            issues.append(f"重复条目 x{cnt}: {key}")

    if not issues:
        print(f"检查通过：{len(roles)} 条角色，无问题。")
        return
    print(f"发现 {len(issues)} 个问题（同类合并，最多显示 50 条）:")
    shown = set()
    n = 0
    for line in issues:
        if line in shown:
            continue
        shown.add(line)
        print("  - " + line)
        n += 1
        if n >= 50:
            print("  ...（其余略）")
            break


def run_suggest(kb_path: Path) -> None:
    """疑似匹配建议：扫描 work=Unknown 的文件夹，按包含关系给出候选，确认后写入别名。"""
    data = load_kb_json(kb_path)
    build_work_index(data)
    # 角色库 = 数据库条目（旧 build_kb 文件夹扫描已删除，纯手工维护时代数据库为权威）
    roles = list(data.get("roles") or [])
    cn_idx, en_idx, en_to_cn, cn_to_en = build_indexes(roles)
    cn_keys = sorted([k for k in cn_idx if len(k) >= 2 and "_" not in k],
                     key=len, reverse=True)
    # 允许连字符（misaka-mikoto 等标准英文名），排除下划线（多段串如 padoru_hakurei-...）
    en_keys = sorted([k for k in en_idx if len(k) >= 4 and "_" not in k],
                     key=len, reverse=True)

    suggestions: list[tuple] = []
    no_cand: list[tuple] = []
    for d in get_target_dirs(None):
        r = resolve_name3(d.name, roles, en_to_cn, cn_to_en)
        if r["work"] != "Unknown" or not (r["zh"] or r["en"]):
            continue
        cands: list[tuple] = []
        if r["zh"]:
            for k in cn_keys:
                if k in r["zh"] or r["zh"] in k:
                    ws = cn_idx[k]
                    if len(ws) == 1:
                        cands.append((k, next(iter(ws)), "zh"))
        if r["en"]:
            enk = normalize_en_key(r["en"])
            if enk:
                for k in en_keys:
                    if k in enk or enk in k:
                        ws = en_idx[k]
                        if len(ws) == 1:
                            cands.append((k, next(iter(ws)), "en"))
        seen_c = set()
        shown = 0
        for canonical, work, kind in cands:
            key = (canonical, work)
            if key in seen_c:
                continue
            seen_c.add(key)
            suggestions.append((d.name, r, canonical, work, kind))
            shown += 1
            if shown >= 3:
                break
        if not seen_c:
            no_cand.append((d.name, r))

    if not suggestions:
        print("没有发现疑似匹配。")
    else:
        print(f"发现 {len(suggestions)} 条疑似匹配（y=接受写入别名, n=跳过, q=退出）:")
        accepted = 0
        for i, (folder, r, canonical, work, kind) in enumerate(suggestions, 1):
            if kind == "zh":
                alias = r["zh"]
                desc = f"中文名「{alias}」包含/被包含于「{canonical}」"
            else:
                alias = r["en"]
                desc = f"英文名「{alias}」包含/被包含于「{canonical}」"
            ans = ask(f"[{i}/{len(suggestions)}] {folder}  {desc} -> {canonical} ({work})? (y/n/q): ").lower()
            if ans in ("q", "quit"):
                break
            if ans not in ("y", "yes"):
                continue
            # 别名直接并入 roles 对应条目的 cn/en 数组（规范名保持首位）
            target = None
            for r in data.get("roles") or []:
                if r.get("work") != work:
                    continue
                lst = role_names(r, kind)
                if kind == "zh" and canonical in lst:
                    target = r
                    break
                if kind == "en" and any(normalize_en_key(x) == normalize_en_key(canonical)
                                        for x in lst):
                    target = r
                    break
            if target is None:
                print(f"  [跳过] roles 中未找到 {canonical} ({work})，请先 --add 添加该角色")
                continue
            lst = role_names(target, kind)
            if any(normalize_en_key(x) == normalize_en_key(alias) for x in lst):
                print(f"  已存在别名 {alias}，跳过")
                continue
            lst.append(alias)
            target[kind] = lst
            accepted += 1
            print(f"  已并入 roles: [{kind}] {alias} -> {canonical} ({work})")
        if accepted:
            save_kb_json(kb_path, data)
            print(f"共并入 {accepted} 条别名到 roles，已保存: {kb_path}")
        else:
            print("未登记任何别名。")

    if no_cand:
        print(f"\n另有 {len(no_cand)} 个 Unknown 文件夹无候选，需手工补充"
              f"（--add 添加对照，或直接编辑 roles JSON；仅显示前 30 条）:")
        for name, _r in no_cand[:30]:
            print(f"  {name}")
        if len(no_cand) > 30:
            print(f"  ...（其余 {len(no_cand) - 30} 条略）")


def work_display_name(work: str, works: dict) -> str:
    """作品键 -> '全称 (键)'。优先中文名，其次英文名；无全称时只显示键。"""
    v = works.get(work) if isinstance(works, dict) else None
    if isinstance(v, dict):
        for lang in ("zh", "en"):
            names = v.get(lang) or []
            if names:
                return f"{names[0]} ({work})"
    elif isinstance(v, list) and v:
        return f"{v[0]} ({work})"
    return work


def format_pair_lines(r1: dict, r2: dict, works: dict) -> str:
    """重复条目对的提示排版：游戏全称在上层，中/英文名按条目成行对齐。

    每个条目一行：cn 别名（逗号连接）在左、en 别名（逗号连接）在右，
    两列左对齐——同一角色的多种写法并列展示，不产生空行错位。
    """
    rows: list[tuple[str, str]] = []
    for r in (r1, r2):
        rows.append((", ".join(role_names(r, "zh")),
                     ", ".join(role_names(r, "en"))))
    cn_w = max((len(c) for c, _ in rows), default=0)
    lines = [f"Game: {work_display_name(r1.get('work', '?'), works)}"]
    for cn, en in rows:
        lines.append(f"  {cn:<{cn_w}} | {en}")
    return "\n".join(lines)


def pair_skip_key(r1: dict, r2: dict) -> str:
    """条目对的稳定键：两个 role_key 排序后用 ↔ 连接（顺序无关）。"""
    return " ↔ ".join(sorted([role_key(r1), role_key(r2)]))


def load_merge_skips(kb_path: Path) -> list[str]:
    """读取已确认不合并的条目对（merge_skips.json）。"""
    p = kb_path / "merge_skips.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data) if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_merge_skips(kb_path: Path, skips: list[str]) -> None:
    """写回跳过记录（去重排序）。"""
    p = kb_path / "merge_skips.json"
    p.write_text(json.dumps(sorted(set(skips)), ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def prune_merge_skips(skips: list[str], roles: list[dict]) -> list[str]:
    """清理失效的跳过记录，只保留"当前仍会建议合并"的对：

    - 条目对任一侧已不在 roles（被合并/删除）-> 移除；
    - 条目仍在但已不再构成子串重叠（知识库更新后关系消失）-> 移除。

    这样 merge_skips 始终收敛为"当前有效的拒绝集合"，不会随历史操作无限堆积。
    """
    by_key = {role_key(r): r for r in roles}
    out: list[str] = []
    for k in skips:
        parts = [p for p in k.split(" ↔ ") if p]
        if len(parts) != 2 or parts[0] not in by_key or parts[1] not in by_key:
            continue
        if not has_substr_overlap(by_key[parts[0]], by_key[parts[1]]):
            continue
        out.append(k)
    return out


def _cn_set(r: dict) -> set[str]:
    """条目 cn 名称集合（去空）。"""
    return {c for c in role_names(r, "zh") if c}


def _en_set(r: dict) -> set[str]:
    """条目 en 名称集合（归一化后）。"""
    return {normalize_en_key(e) for e in role_names(r, "en") if e}


def has_substr_overlap(r1: dict, r2: dict) -> bool:
    """两个条目是否构成子串重叠（cn>=2 / en>=3 的一方是另一方子串）。

    与 run_merge 阶段 2 的手动确认条件一致；prune_merge_skips 据此判断
    跳过记录是否仍有效。
    """
    for a in _cn_set(r1):
        for b in _cn_set(r2):
            if a != b and len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
                return True
    for a in _en_set(r1):
        for b in _en_set(r2):
            if a != b and len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
                return True
    return False


def run_merge(kb_path: Path) -> None:
    """合并重复角色条目（两阶段）。

    阶段 1（自动）：cn 或 en 有完全相等项的两条 = 确定同一角色，直接并入；
    阶段 2（手动）：仅子串/简称重叠的对，逐对 y/n 确认（不再整组闭包合并，
                   避免如 hina 是 hinata/hiyori 共同子串导致的误并）。
    合并时 cn/en 数组按名称长度降序（全称在前，作为规范名/补全默认值）。
    """
    data = load_kb_json(kb_path)
    roles = data.get("roles") or []
    if not roles:
        print("知识库为空。")
        return

    def cn_set(r):
        return _cn_set(r)

    def en_set(r):
        return _en_set(r)

    def has_exact(r1, r2):
        return bool(cn_set(r1) & cn_set(r2) or en_set(r1) & en_set(r2))

    def has_substr(r1, r2):
        """仅子串重叠（不含完全相等）：cn>=2 或 en>=3 的一方是另一方的子串。"""
        return has_substr_overlap(r1, r2)

    def merge_into(base: dict, other: dict) -> None:
        """把 other 并入 base（cn/en 数组去重，按长度降序全称在前），随后移除 other。"""
        for f in ("zh", "en"):
            merged = []
            for x in role_names(base, f) + role_names(other, f):
                if x and x not in merged:
                    merged.append(x)
            merged.sort(key=len, reverse=True)
            base[f] = merged

    def describe(r: dict) -> str:
        return f"{r.get('zh') or '-'} | {r.get('en') or '-'} ({r.get('work', '?')})"

    # 阶段 1：自动合并（cn 或 en 有完全相等项，且同一作品）
    auto_count = 0
    i = 0
    while i < len(roles):
        j = i + 1
        while j < len(roles):
            if roles[i].get("work") == roles[j].get("work") and has_exact(roles[i], roles[j]):
                print(f"自动合并: {describe(roles[j])}  ->  {describe(roles[i])}")
                merge_into(roles[i], roles[j])
                roles.pop(j)
                auto_count += 1
            else:
                j += 1
        i += 1

    # 阶段 2：手动确认（仅子串重叠，避免误并）
    works = data.get("works") or {}
    skips = prune_merge_skips(load_merge_skips(kb_path), roles)
    manual_count = 0
    i = 0
    while i < len(roles):
        j = i + 1
        while j < len(roles):
            if roles[i].get("work") == roles[j].get("work") and has_substr(roles[i], roles[j]):
                if pair_skip_key(roles[i], roles[j]) in skips:
                    j += 1  # 用户此前已确认"不合并"，不再询问
                    continue
                print("[子串重叠]")
                print(format_pair_lines(roles[i], roles[j], works))
                ans = ask("(y=合并, n=跳过, q=退出): ").lower()
                if ans in ("q", "quit"):
                    save_merge_skips(kb_path, skips)
                    if auto_count or manual_count:
                        data["roles"] = roles
                        save_kb_json(kb_path, data)
                        print(f"已保存（自动 {auto_count} 条，手动 {manual_count} 条）: {kb_path}")
                    return
                if ans in ("y", "yes"):
                    print(f"手动合并: {describe(roles[j])}  ->  {describe(roles[i])}")
                    merge_into(roles[i], roles[j])
                    roles.pop(j)
                    manual_count += 1
                else:
                    skips.append(pair_skip_key(roles[i], roles[j]))
                    j += 1
            else:
                j += 1
        i += 1

    data["roles"] = roles
    save_merge_skips(kb_path, skips)
    if auto_count or manual_count:
        save_kb_json(kb_path, data)
        print(f"合并完成: 自动 {auto_count} 条，手动 {manual_count} 条，已保存: {kb_path}")
    else:
        print("没有发现可合并的重复条目。")


def build_indexes(roles: list[dict], priority_roles: list[dict] | None = None):
    """返回 (cn_idx, en_idx, en_to_cn, cn_to_en)。后两者用于补全/标准化中英文名。

    角色条目的 cn/en 可以是字符串或数组：数组第一个为规范名（补全/标准化用它），
    其余为别名（仅用于匹配）。别名已并入 cn/en 数组，不再单独维护。
    priority_roles：用户最近明确选择的条目（如交互学习刚收录的），强制单作品归属，
    用于解决跨作品同名歧义（如数据库同时有 GF/GF2 的「夏安」，用户选 GF 后归 GF）。"""
    cn_idx: dict[str, set] = {}
    en_idx: dict[str, set] = {}
    en_to_cn: dict[str, list] = {}
    cn_to_en: dict[str, list] = {}
    for r in roles:
        cn_list = role_names(r, "zh")
        en_list = role_names(r, "en")
        cn_main = cn_list[0] if cn_list else ""
        en_main = en_list[0] if en_list else ""
        for c in cn_list:
            cn_idx.setdefault(c, set()).add(r["work"])
        for e in en_list:
            key = normalize_en_key(e)
            en_idx.setdefault(key, set()).add(r["work"])
            en_idx.setdefault(key.replace('_', '-'), set()).add(r["work"])
        if cn_main and en_main:
            # 规范名与所有别名都可触发补全，补全值统一取规范名（第一个）
            en_to_cn.setdefault(normalize_en_key(en_main), []).append((r["work"], cn_main))
            cn_to_en.setdefault(cn_main, []).append((r["work"], en_main))
            for c in cn_list:
                if c != cn_main:
                    cn_to_en.setdefault(c, []).append((r["work"], en_main))
            for e in en_list:
                if e != en_main:
                    key = normalize_en_key(e)
                    en_to_cn.setdefault(key, []).append((r["work"], cn_main))
                    en_to_cn.setdefault(key.replace('_', '-'), []).append((r["work"], cn_main))
    # 用户最近明确选择（如交互学习收录）的条目强制单作品归属：
    # 解决跨作品同名歧义（用户选定后归该作品）
    for r in (priority_roles or []):
        for c in role_names(r, "zh"):
            cn_idx[c] = {r["work"]}
        for e in role_names(r, "en"):
            key = normalize_en_key(e)
            en_idx[key] = {r["work"]}
            en_idx[key.replace('_', '-')] = {r["work"]}
        cn_list = role_names(r, "zh")
        en_list = role_names(r, "en")
        if cn_list and en_list:
            cn_to_en[cn_list[0]] = [(r["work"], en_list[0])]
            en_to_cn[normalize_en_key(en_list[0])] = [(r["work"], cn_list[0])]
    return cn_idx, en_idx, en_to_cn, cn_to_en


_AUTHOR_DIR_RE = re.compile(r"\d{4}(?:[-_].*)?\Z")


def _is_author_dir(name: str) -> bool:
    """是否作者目录：以 4 位编号开头（如 0040 或 0040-碎de帆）。

    作者目录直接收集其全部子目录作为模型目录（含纯 zip 压缩包模型），
    因此识别必须宽松到带名字后缀的情况，否则这些模型会被漏掉。
    """
    return bool(_AUTHOR_DIR_RE.match(name))


def get_target_dirs(path: str | None) -> list[Path]:
    """扫描目标目录：Models/<作者>/<模型> 两层 + 根下 <作者>/<模型> 两层 + Other-YSM-Models。

    作者目录（以 4 位编号开头）直接收集其下所有子目录，含纯 zip 压缩包模型
    （zip 模型没有 .ysm 文件，也没有 previews/ 目录，必须按目录收集）。
    """
    roots = [Path(path).resolve()] if path else DEFAULT_ROOTS
    dirs: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        # Models 根：收集每个作者（编号开头）下所有子目录（含纯 zip 压缩包模型）
        if root.name == "Models":
            for author in sorted(root.iterdir()):
                if not (author.is_dir() and _is_author_dir(author.name)):
                    continue
                for model in sorted(author.iterdir()):
                    if model.is_dir() and model.name != "previews":
                        dirs.append(model)
        elif _is_author_dir(root.name):
            # 作者目录（Models/<作者> 或仓库根下的 <作者>，如 0040-碎de帆）：
            # 直接收集其下所有子目录（不含 previews），含纯 zip 压缩包模型
            for model in sorted(root.iterdir()):
                if model.is_dir() and model.name != "previews":
                    dirs.append(model)
        else:
            # Other-YSM-Models 等：现在按 <作品>/<模型> 两层组织（兼容一层/混合），
            # 递归收集含 .ysm 文件（或 previews/ 子目录）的目录作为模型目录。
            for sub in sorted(root.iterdir()):
                if sub.is_dir() and sub.name != "previews":
                    _collect_model_dirs(sub, dirs)
    return sorted(set(dirs), key=lambda d: str(d))


def _collect_model_dirs(d: Path, out: list[Path]) -> None:
    """递归收集模型目录：含 .ysm 文件或 previews/ 子目录即视为模型目录。

    适配 Other-YSM-Models 的 <作品>/<模型> 两层（或更深）组织；
    作品层（无 .ysm）继续向下找，避免把 AK/、BA/ 等作品目录误当模型。
    """
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    has_ysm = any(e.is_file() and e.suffix.lower() == ".ysm" for e in entries)
    has_previews = any(e.is_dir() and e.name == "previews" for e in entries)
    if has_ysm or has_previews:
        out.append(d)
        return
    for e in entries:
        if e.is_dir() and e.name != "previews":
            _collect_model_dirs(e, out)


# ---------------------------------------------------------------------------
# 作品版命令（操作 × 对象：作品侧；与角色的 list/del/check/merge/set-default 对应）
# ---------------------------------------------------------------------------
def _work_line(i: int, wk: str, meta: dict, role_count: int) -> str:
    """作品一行摘要：编号 | 键 | en 首项 | cn 首项 | 角色数。"""
    en = meta.get("en") or []
    cn = meta.get("zh") or []
    en_s = en[0] if en else "-"
    cn_s = cn[0] if cn else "-"
    return f"[{i}] {wk:<16} | {en_s} | {cn_s} | 角色 {role_count}"


def _role_count_map(data: dict) -> dict[str, int]:
    """按作品统计角色数（供作品列表显示）。"""
    counts: dict[str, int] = {}
    for r in (data.get("roles") or []):
        w = str(r.get("work", ""))
        counts[w] = counts.get(w, 0) + 1
    return counts


def list_works_cmd(kb_path: Path) -> None:
    """列出全部作品（键 + en/cn 首项 + 角色数）。"""
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    counts = _role_count_map(data)
    if not works:
        print("知识库暂无作品。")
        return
    print(f"作品 {len(works)} 个：")
    for i, (wk, meta) in enumerate(sorted(works.items()), 1):
        print("  " + _work_line(i, wk, meta or {}, counts.get(wk, 0)))
    print(f"共 {len(works)} 个作品。")


def del_works_cmd(kb_path: Path) -> None:
    """交互删除作品：列出 -> 选择 -> 确认（连同该作品全部角色一起删除）。"""
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    if not works:
        print("知识库暂无作品。")
        return
    print("删除作品：列出全部作品，选编号删除（连同该作品角色）。")
    while True:
        print("-" * 50)
        items = sorted(works.items())
        counts = _role_count_map(data)
        for i, (wk, meta) in enumerate(items, 1):
            print("  " + _work_line(i, wk, meta or {}, counts.get(wk, 0)))
        sel = ask("选择要删除的作品编号（Enter=返回, q=退出）: ").strip()
        if sel.lower() in ("q", "quit"):
            break
        if not sel.isdigit() or not (1 <= int(sel) <= len(items)):
            print("编号无效，返回。")
            break
        wk = items[int(sel) - 1][0]
        role_count = counts.get(wk, 0)
        confirm = ask(f"确认删除作品 {wk!r}（含 {role_count} 个角色）？(y=确认, 其他=取消): ").strip()
        if confirm.lower() not in ("y", "yes"):
            print("已取消。")
            continue
        works.pop(wk, None)
        data["roles"] = [r for r in (data.get("roles") or [])
                         if str(r.get("work", "")) != wk]
        save_kb_json(kb_path, data)
        print(f"已删除作品 {wk!r}（含 {role_count} 个角色）。")


def check_works_cmd(kb_path: Path) -> None:
    """作品数据检查：转义键冲突、缺 category、缺英文名、角色 work 悬空。"""
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    roles = data.get("roles") or []
    issues: list[str] = []
    # 转义冲突（_safe_name 撞名会覆盖文件）
    seen: dict[str, str] = {}
    for wk in works:
        fname = storage._safe_name(wk)
        prev = seen.get(fname)
        if prev and prev != wk:
            issues.append(f"转义冲突: {prev!r} 与 {wk!r} 同为文件 {fname}.json")
        seen[fname] = wk
    for wk, meta in works.items():
        if not isinstance(meta, dict):
            continue
        if not meta.get("category"):
            issues.append(f"缺 category: {wk!r}")
        if not (meta.get("en") or []):
            issues.append(f"缺英文名: {wk!r}")
    # 角色 work 悬空（不在作品表）
    work_keys = set(works)
    for r in roles:
        w = str(r.get("work", ""))
        if w and w not in work_keys:
            cn_s = " / ".join(r.get("zh") or []) or "?"
            en_s = " / ".join(r.get("en") or []) or "?"
            issues.append(f"角色 work 悬空: {cn_s} / {en_s} -> {w!r}")
    if issues:
        print(f"作品检查发现 {len(issues)} 个问题：")
        for msg in issues:
            print("  - " + msg)
        return
    print(f"作品检查通过（{len(works)} 个作品）。")


def _pick_work(works: dict, prompt: str = "搜索作品（键/en/cn/ja 关键词，q=退出）: ") -> tuple[str, dict] | None:
    """搜索并选择一个作品，返回 (作品键, meta)；取消返回 None。

    供 set-default / edit / merge 等作品交互命令复用搜索选择逻辑。
    """
    while True:
        print("-" * 50)
        kw = ask(prompt).strip()
        if kw.lower() in ("q", "quit"):
            return None
        if not kw:
            print("请输入搜索关键词。")
            continue
        hits = {wk: meta for wk, meta in works.items()
                if kw.lower() in wk.lower()
                or any(kw.lower() in str(x).lower()
                       for x in (meta.get("en") or []) + (meta.get("zh") or [])
                       + (meta.get("ja") or []))}
        if not hits:
            print("未找到匹配作品。")
            continue
        items = sorted(hits.items())
        for i, (wk, meta) in enumerate(items, 1):
            en_s = " / ".join(meta.get("en") or []) or "-"
            cn_s = " / ".join(meta.get("zh") or []) or "-"
            print(f"  [{i}] {wk:<16} | en: {en_s} | cn: {cn_s}")
        sel = ask("选择编号（Enter=重新搜索，q=退出）: ").strip()
        if sel.lower() in ("q", "quit"):
            return None
        if not sel.isdigit() or not (1 <= int(sel) <= len(items)):
            print("无效编号。")
            continue
        return items[int(sel) - 1]


def edit_work_cmd(kb_path: Path) -> None:
    """交互编辑作品：名称（别名增删改/排序/设默认） / 大类 / 作品键。

    复用 _edit_names 的名称列表编辑能力（含 s=设为默认、r=重新排序），
    保存时同步 character/*.json 与根 README 分类区块。
    """
    data = load_kb_json(kb_path)
    works = data.setdefault("works", {})
    if not works:
        print("知识库暂无作品。")
        return
    print("编辑作品：搜索作品 -> 选择 -> 编辑名称/大类/作品键。")
    picked = _pick_work(works)
    if picked is None:
        return
    wk, meta = picked
    meta = dict(meta)
    while True:
        print("-" * 56)
        en_s = " / ".join(meta.get("en") or []) or "-"
        cn_s = " / ".join(meta.get("zh") or []) or "-"
        ja_s = " / ".join(meta.get("ja") or []) or "-"
        print(f"编辑作品: {wk}")
        print(f"  en: {en_s}")
        print(f"  zh: {cn_s}")
        print(f"  ja: {ja_s}")
        print(f"  category: {meta.get('category') or '-'}")
        print("  1) 英文名(别名/排序)  2) 中文名(别名/排序)  3) 日文名(别名/排序)")
        print("  4) 大类  5) 作品键  0) 返回")
        sel = ask("选择: ").strip()
        if sel in ("0", "q", "quit"):
            # 有改动才保存
            if works.get(wk) != meta:
                works[wk] = meta
                _save_works_and_category(kb_path, data)
                print(f"已保存作品 {wk!r}。")
            return
        if sel == "1":
            _edit_names(meta, "en", "英文名")
        elif sel == "2":
            _edit_names(meta, "zh", "中文名")
        elif sel == "3":
            _edit_names(meta, "ja", "日文名")
        elif sel == "4":
            cur = meta.get("category") or "Other"
            cat = ask(f"大类 [{cur}] ({'/'.join(CATEGORIES)}): ").strip().capitalize()
            if cat.lower() in ("q", "quit"):
                continue
            if not cat:
                cat = cur
            if cat not in CATEGORIES:
                print(f"未知大类 '{cat}'，可选: {', '.join(CATEGORIES)}")
                continue
            meta["category"] = cat
            print(f"  大类已改为: {cat}")
        elif sel == "5":
            new_key = ask(f"作品键 [{wk}] (改后角色 work 一并更新): ").strip()
            if new_key.lower() in ("q", "quit"):
                continue
            new_key = new_key.strip()
            if not new_key:
                continue
            if new_key == wk:
                continue
            if new_key in works:
                print(f"作品键 '{new_key}' 已存在。")
                continue
            # 迁移所有角色的 work 字段
            for r in data.get("roles") or []:
                if str(r.get("work", "")) == wk:
                    r["work"] = new_key
            works[new_key] = works.pop(wk)
            print(f"  作品键已改为: {new_key}（角色 work 已同步）")
            wk = new_key
        else:
            print("无效选择。")


def set_default_work_cmd(kb_path: Path) -> None:
    """交互设定作品显示名：搜索/选择作品 -> 选已有名称或输入新名（en/cn/ja 首项）。

    与角色的 --set-default 同理：默认名 = 数组首项；新输入的名称加入数组。
    作品显示名用于 README 标签；作品键（文件夹前缀）不受影响。
    """
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    if not works:
        print("知识库暂无作品。")
        return
    print("设定作品显示名：搜索作品 -> 选择 -> 选已有名称或输入新名称（写入数组首项）。")
    while True:
        print("-" * 50)
        kw = ask("搜索作品（键/en/cn/ja 关键词，q=退出）: ").strip()
        if kw.lower() in ("q", "quit"):
            break
        if not kw:
            print("请输入搜索关键词。")
            continue
        hits = {wk: meta for wk, meta in works.items()
                if kw.lower() in wk.lower()
                or any(kw.lower() in str(x).lower()
                       for x in (meta.get("en") or []) + (meta.get("zh") or [])
                       + (meta.get("ja") or []))}
        if not hits:
            print("未找到匹配作品。")
            continue
        items = sorted(hits.items())
        for i, (wk, meta) in enumerate(items, 1):
            en_s = " / ".join(meta.get("en") or []) or "-"
            cn_s = " / ".join(meta.get("zh") or []) or "-"
            print(f"  [{i}] {wk:<16} | en: {en_s} | cn: {cn_s}")
        sel = ask("选择编号（Enter=跳过）: ").strip()
        if sel.lower() in ("q", "quit"):
            break
        if not sel.isdigit() or not (1 <= int(sel) <= len(items)):
            print("编号无效，跳过。")
            continue
        wk = items[int(sel) - 1][0]
        meta = dict(works.get(wk) or {})
        for field, label in (("en", "英文名"), ("zh", "中文名"), ("ja", "日文名")):
            cur = meta.get(field) or []
            cur = [x for x in (cur if isinstance(cur, list) else [cur]) if x]
            if cur:
                print(f"  当前 {label}（数组首项=默认名）: {' / '.join(cur)}")
                for i, n in enumerate(cur, 1):
                    print(f"    [{i}] {n}")
            else:
                print(f"  当前 {label}（空）")
            val = ask(f"  选编号=设默认{label}，或输入新{label}（Enter=不改，q=退出）: ").strip()
            if val.lower() in ("q", "quit"):
                return
            if val.isdigit() and cur and 1 <= int(val) <= len(cur):
                chosen = cur[int(val) - 1]
            elif val:
                chosen = val
            else:
                continue
            meta[field] = [chosen] + [n for n in cur if n != chosen]
        works[wk] = meta
        save_kb_json(kb_path, data)
        print(f"已设定作品 {wk!r} 显示名：en={meta.get('en')} cn={meta.get('zh')} ja={meta.get('ja')}")


def merge_works_cmd(kb_path: Path) -> None:
    """交互合并作品：按名称(en/cn/ja)或角色名重叠提候选 -> 逐个确认 -> 选主键合并。

    合并 = 把被合并作品的所有角色 work 归到主键，元数据(名称/category)并入主键，
    删除被合并作品（皮肤表键同步迁移）。仅当名称/角色确有重叠时才提示，避免误合并。
    """
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    roles = data.get("roles") or []
    if len(works) < 2:
        print("作品不足 2 个，无需合并。")
        return

    def norm(s) -> str:
        return re.sub(r"[\s_\-]+", "", str(s)).lower()

    # 作品名称 -> 出现过的作品（en/cn/ja 各名称）
    name_map: dict[str, list[str]] = {}
    for wk, meta in works.items():
        if not isinstance(meta, dict):
            continue
        for field in ("en", "zh", "ja"):
            for x in (meta.get(field) or []):
                key = norm(x)
                if key:
                    name_map.setdefault(key, []).append(wk)
    # 角色名 -> 作品（跨作品同名角色可能暗示作品重复）
    role_name_map: dict[str, list[str]] = {}
    for r in roles:
        for x in (r.get("zh") or []) + (r.get("en") or []):
            key = norm(x)
            if key and len(key) >= 2:
                role_name_map.setdefault(key, []).append(str(r.get("work", "")))

    # 提候选对：两个作品共享名称或共享角色名
    pairs: dict[tuple[str, str], str] = {}
    for key, ws in name_map.items():
        uniq = sorted(set(ws))
        if len(uniq) == 2:
            pairs.setdefault(tuple(uniq), "名称重叠")
    for key, ws in role_name_map.items():
        uniq = sorted(set(ws))
        if len(uniq) == 2:
            pairs.setdefault(tuple(uniq), "角色名重叠")

    if not pairs:
        print("未发现疑似重复的作品（无名称/角色名重叠）。")
        return
    print(f"发现 {len(pairs)} 对疑似重复作品（按 名称/角色名 重叠）：")
    merged_any = False
    for (a, b) in sorted(pairs):
        print("-" * 50)
        ma = works.get(a) or {}
        mb = works.get(b) or {}
        print(f"  候选: {a!r} vs {b!r}（{pairs[(a, b)]}）")
        print(f"    {a}: en={ma.get('en') or '-'} cn={ma.get('zh') or '-'}")
        print(f"    {b}: en={mb.get('en') or '-'} cn={mb.get('zh') or '-'}")
        ans = ask("  合并吗？（1=保留前者为主键, 2=保留后者为主键, Enter=跳过, q=退出）: ").strip()
        if ans.lower() in ("q", "quit"):
            break
        if ans not in ("1", "2"):
            print("  跳过。")
            continue
        keep, drop = (a, b) if ans == "1" else (b, a)
        confirm = ask(f"  确认把 {drop!r} 并入 {keep!r}（其角色全归 {keep}）？(y=确认): ").strip()
        if confirm.lower() not in ("y", "yes"):
            print("  已取消。")
            continue
        # 元数据合并：keep 缺失字段从 drop 补（drop 若有则并入名称，去重）
        keep_meta = dict(works.get(keep) or {})
        if drop in works:
            drop_meta = dict(works.pop(drop) or {})
            for field in ("en", "zh", "ja"):
                cur = keep_meta.get(field) or []
                add = drop_meta.get(field) or []
                merged = list(cur) + [x for x in add if x not in cur]
                if merged:
                    keep_meta[field] = merged
            if not keep_meta.get("category"):
                keep_meta["category"] = drop_meta.get("category")
        works[keep] = keep_meta
        # 角色 work 迁移
        for r in roles:
            if str(r.get("work", "")) == drop:
                r["work"] = keep

        save_kb_json(kb_path, data)
        print(f"  已合并: {drop!r} -> {keep!r}")
        merged_any = True
    print(f"合并完成：{'有合并' if merged_any else '未做任何合并'}。")


# ---------------------------------------------------------------------------
# 作品维护：保存 + 分类区块 / 添加 / 默认名 / 重命名键
# （原 check&fix/kb_tool.py 中的实现，下沉至此，让 kb_tool.py 只做 CLI 入口）
# ---------------------------------------------------------------------------
def _save_works_and_category(kb_path: Path, data: dict) -> None:
    """保存 character/*.json（合并格式）+ 更新根 README 模型分类区块（不落盘 category_map.json）。"""
    save_kb_json(kb_path, data)
    cat_map = build_category_map(data)
    print(f"已保存作品知识库: {kb_path / 'character'}")
    total = sum(len(v) for v in cat_map.values())
    print(f"分类（从 character/*.json 现算）: {total} 个作品分布在 {len(cat_map)} 个大类")
    changed, action = update_readme_works_section(REPO_ROOT / "README.md", data)
    if changed:
        print(f"根 README 模型分类区块已{action}")


def add_work_interactive(kb_path: Path) -> int:
    """交互式添加新作品（character/*.json 权威源，自动重建分类与 README 区块）。"""
    data = load_kb_json(kb_path)
    works = data.setdefault("works", {})
    print("交互式添加新作品：逐项输入，回车跳过；输入 q 结束。")
    print(f"大类: {', '.join(CATEGORIES)}")
    added = 0
    while True:
        print("-" * 40)
        key = ask("作品键 (必填，唯一，如 BA/AK/OC): ")
        if key.lower() in ("q", "quit"):
            break
        key = key.strip()
        if not key:
            print("作品键不能为空，本条跳过。")
            continue
        if key in works:
            print(f"作品 '{key}' 已存在，跳过（编辑请用 work edit）。")
            continue
        en = ask("英文名 (逗号分隔，至少一个): ")
        if en.lower() in ("q", "quit"):
            break
        en_list = [x.strip() for x in en.split(",") if x.strip()]
        if not en_list:
            print("英文名至少填一个，本条跳过。")
            continue
        cn = ask("中文名 (逗号分隔，可空): ")
        if cn.lower() in ("q", "quit"):
            break
        cn_list = [x.strip() for x in cn.split(",") if x.strip()]
        ja = ask("日文名 (逗号分隔，可空): ")
        if ja.lower() in ("q", "quit"):
            break
        ja_list = [x.strip() for x in ja.split(",") if x.strip()]
        cat = ask(f"大类 ({'/'.join(CATEGORIES)}，默认 Other): ")
        if cat.lower() in ("q", "quit"):
            break
        cat = cat.strip().capitalize() if cat.strip() else "Other"
        if cat not in CATEGORIES:
            print(f"未知大类 '{cat}'，本条跳过。")
            continue
        entry: dict = {"en": en_list}
        if cn_list:
            entry["zh"] = cn_list
        if ja_list:
            entry["ja"] = ja_list
        entry["category"] = cat
        works[key] = entry
        save_kb_json(kb_path, data)  # 每条约保存（防中断丢失）
        added += 1
        print(f"已添加作品: {key} | {', '.join(en_list)} | {cat}")
    if added:
        _save_works_and_category(kb_path, data)
    print(f"共添加 {added} 个作品。")
    return 0


def _pick_default_name(field_label: str, current, ask_fn) -> tuple[list | None, str | None]:
    """交互选择/输入默认名：展示已有名称（编号）供选择，或输入新名称。

    返回 (新数组, 选中的名称)；跳过（Enter）返回 (原数组, None)；取消（q）返回 (None, None)。
    新输入的名称加入数组并设为默认名（首项），原名称自动降为别名。
    """
    names = [n for n in (current if isinstance(current, list) else [current]) if n]
    if names:
        print(f"  当前 {field_label}（数组首项=默认名）: {' / '.join(names)}")
        for i, n in enumerate(names, 1):
            print(f"    [{i}] {n}")
    else:
        print(f"  当前 {field_label}（空）")
    val = ask_fn(f"  选编号=设为默认名，或输入新{field_label}（将加入数组并设为默认名；"
                 f"Enter=不改，q=退出）: ").strip()
    if val.lower() in ("q", "quit"):
        return None, None
    if val.isdigit() and names and 1 <= int(val) <= len(names):
        chosen = names[int(val) - 1]
    elif val:
        chosen = val
    else:
        return names, None
    new_names = [chosen] + [n for n in names if n != chosen]
    return new_names, chosen


def set_default_role_cmd(kb_path: Path) -> int:
    """交互式设定角色默认中英文名：搜索角色 -> 选择 -> 选已有名称或添加新名称。

    默认名 = zh/en 数组首项；02 重命名自动把该角色统一为默认名
    （由 resolve_name3 的"标准化"实现，如 Chuyin -> Miku）。
    新输入的名称会加入数组（成为该角色名称之一），原名称自动降为别名。
    """
    data = load_kb_json(kb_path)
    roles = data.get("roles") or []
    if not roles:
        print("知识库为空，请先使用 --roles / --add-role 添加角色。")
        return 0
    print("设定角色默认名：搜索角色 -> 选择 -> 选已有名称或输入新名称（新名称自动加入别名）。")
    while True:
        print("-" * 50)
        kw = ask("搜索角色（中文/英文/作品关键词，q=退出）: ")
        if kw.lower() in ("q", "quit"):
            break
        if not kw:
            print("请输入搜索关键词。")
            continue
        hits = [r for r in roles
                if kw.lower() in str(r.get("zh", "")).lower()
                or kw.lower() in str(r.get("en", "")).lower()
                or kw.lower() in str(r.get("work", "")).lower()]
        if not hits:
            print("未找到匹配条目。")
            continue
        print(f"命中 {len(hits)} 条：")
        for i, r in enumerate(hits, 1):
            cn_s = " / ".join(r.get("zh") or []) or "-"
            en_s = " / ".join(r.get("en") or []) or "-"
            print(f"  [{i}] {r.get('work', ''):<12} | cn: {cn_s} | en: {en_s}")
        sel = ask("选择编号（Enter=跳过）: ")
        if sel.lower() in ("q", "quit"):
            break
        if not sel.isdigit() or not (1 <= int(sel) <= len(hits)):
            print("编号无效，跳过。")
            continue
        r = hits[int(sel) - 1]
        new_cn, cn_chosen = _pick_default_name("中文名", r.get("zh") or [], ask)
        if new_cn is None:
            break
        new_en, en_chosen = _pick_default_name("英文名", r.get("en") or [], ask)
        if new_en is None:
            break
        if cn_chosen is None and en_chosen is None:
            print("中文名和英文名都未修改，本条跳过。")
            continue
        r["zh"] = new_cn
        r["en"] = new_en
        save_kb_json(kb_path, data)
        print(f"已设定默认名: {r.get('work')} | cn 默认={cn_chosen or '不变'}"
              f" | en 默认={en_chosen or '不变'}"
              f"（数组: {r.get('zh')} / {r.get('en')}）")
    print("提示：默认名是 cn/en 数组首项，重命名会把这些角色统一为默认名。")
    return 0


def rename_work_cmd(kb_path: Path, old_key: str, new_key: str,
                    apply_changes: bool = False) -> int:
    """安全重命名作品键：old_key -> new_key，联动更新文件、键、角色 work、皮肤表键。

    默认 dry-run 只预览；加 --apply 才真正写盘。merge_skips 中的旧记录
    会自然失效并被 prune 清理（不迁移，避免误替换名称里的同文字段）。
    """
    old_key = old_key.strip()
    new_key = new_key.strip()
    if old_key == new_key:
        print("旧键与新键相同，无需修改。")
        return 0
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    roles = data.get("roles") or []
    if old_key not in works:
        print(f"错误: 作品键 {old_key!r} 不存在于知识库。")
        return 1
    if new_key in works:
        print(f"错误: 目标作品键 {new_key!r} 已存在，无法重命名（请先处理冲突）。")
        return 1
    role_hits = [r for r in roles if str(r.get("work", "")) == old_key]
    print(f"[{'执行' if apply_changes else '预览'}] 重命名作品键: {old_key} -> {new_key}")
    print(f"  - 文件: {storage._safe_name(old_key)}.json -> {storage._safe_name(new_key)}.json")
    print(f"  - merge_skips: 不迁移（旧记录自然失效并清理）")
    if not apply_changes:
        print("dry-run 预览：未写盘。确认无误请加 --apply 执行。")
        return 0
    works[new_key] = works.pop(old_key)
    for r in roles:
        if str(r.get("work", "")) == old_key:
            r["work"] = new_key

    save_kb_json(kb_path, data)
    print(f"已完成重命名作品键: {old_key} -> {new_key}")
    return 0


def rename_work_interactive(kb_path: Path, apply_changes: bool = False) -> int:
    """交互式重命名作品键：列出作品 -> 选旧键 -> 输入新键 -> 走 rename_work_cmd。"""
    data = load_kb_json(kb_path)
    works = data.get("works") or {}
    if not works:
        print("知识库暂无作品。")
        return 1
    print("重命名作品键：选择要改名的作品，再输入新键。")
    items = sorted(works.items())
    counts = {}
    for r in (data.get("roles") or []):
        counts[str(r.get("work", ""))] = counts.get(str(r.get("work", "")), 0) + 1
    for i, (wk, meta) in enumerate(items, 1):
        en_s = " / ".join(meta.get("en") or []) or "-"
        print(f"  [{i}] {wk:<16} | {en_s} | 角色 {counts.get(wk, 0)}")
    sel = ask("选择要重命名的作品编号（Enter=取消, q=退出）: ").strip()
    if sel.lower() in ("q", "quit") or not sel.isdigit() or not (1 <= int(sel) <= len(items)):
        print("已取消。")
        return 0
    old_key = items[int(sel) - 1][0]
    new_key = ask(f"新作品键（当前 {old_key!r}，Enter=取消, q=退出）: ").strip()
    if not new_key or new_key.lower() in ("q", "quit"):
        print("已取消。")
        return 0
    return rename_work_cmd(kb_path, old_key, new_key, apply_changes=apply_changes)
