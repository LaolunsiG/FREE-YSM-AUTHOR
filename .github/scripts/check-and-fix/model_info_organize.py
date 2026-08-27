#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 知识库维护命令行入口（薄壳）：只负责解析参数并分派，业务实现全部在 lib/kb/。

  lib/kb/cmds.py     角色 / 作品维护（增删改查 / 合并 / 默认名 / 重命名键）
  lib/kb/authors.py  作者维护（合并重复作者 / 推导作者名 / 重建作者数据）

命令统一为「对象 + 动作」两级：
  role   add / del / list / check / merge / suggest / set-default
  work   add / del / list / check / merge / set-default / rename / edit
  author merge / sync / rebuild / check / alias

`role` 不带动作时进入交互式角色管理菜单（增删改查/别名/默认名/皮肤）；
其他对象只输动作时会交互补全：`add` → 让你选对象。

用法:
  python '.github/scripts/check-and-fix/model_info_organize.py' role                      # 进入角色管理菜单
  python '.github/scripts/check-and-fix/model_info_organize.py' role add                  # 添加角色
  python '.github/scripts/check-and-fix/model_info_organize.py' role list                 # 列出角色
  python '.github/scripts/check-and-fix/model_info_organize.py' role merge                # 合并角色（候选确认）
  python '.github/scripts/check-and-fix/model_info_organize.py' work add                  # 添加作品
  python '.github/scripts/check-and-fix/model_info_organize.py' work rename OLD NEW --apply  # 重命名作品键（不给则交互选择）
  python '.github/scripts/check-and-fix/model_info_organize.py' work edit                   # 编辑作品（名称别名/排序/分类/作品键）
  python '.github/scripts/check-and-fix/model_info_organize.py' author merge --apply      # 合并重复作者（候选逐对确认）
  python '.github/scripts/check-and-fix/model_info_organize.py' author sync --apply       # 从 .ysm 推导作者名并入 authors.json
  python '.github/scripts/check-and-fix/model_info_organize.py' author rebuild            # 重建 authors.json

全局参数 --kb DIR 放在对象之前（如 kb_tool.py --kb <DIR> role list）。
模型文件夹重命名请用 02_rename_model_folders.py；本脚本除 author merge 外不改文件夹/文件名。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import console as lib_console
from lib import paths as lib_paths
from lib.kb.authors import (
    add_author_alias, merge_author_info, merge_authors_flow, sync_authors_from_models,
    write_authors_data,
)
from lib.kb.cmds import (
    add_role_cmd, add_work_interactive, check_works_cmd, del_role_cmd,
    del_works_cmd, edit_work_cmd, list_role_cmd, list_works_cmd, merge_works_cmd,
    rename_work_cmd, rename_work_interactive, roles_cmd, run_check, run_merge,
    run_suggest, set_default_role_cmd, set_default_work_cmd,
)

REPO_ROOT = lib_paths.WORKSPACE_ROOT
KB_DEFAULT = lib_paths.MODEL_INFO_DIR

# 对象 → 可用动作（缺动作时的交互补全用）
OBJECTS = ("role", "work", "author")
ACTIONS_BY_OBJECT: dict[str, list[str]] = {
    "role": ["add", "del", "list", "check", "merge", "suggest", "set-default"],
    "work": ["add", "del", "list", "check", "merge", "set-default", "rename", "edit"],
    "author": ["merge", "sync", "rebuild", "check", "alias", "patch"],
}
# 动作 → 可用对象（缺对象时的交互补全用）
ACTION_OBJECTS: dict[str, list[str]] = {}
for _obj, _acts in ACTIONS_BY_OBJECT.items():
    for _a in _acts:
        ACTION_OBJECTS.setdefault(_a, []).append(_obj)


def _pick(prompt: str, choices: list[str]) -> str | None:
    """列出选项供用户选编号或直接输入名称；返回选中项，取消返回 None。"""
    for i, c in enumerate(choices, 1):
        print(f"  [{i}] {c}")
    ans = lib_console.ask(prompt).strip().lower()
    if not ans or ans in ("q", "quit"):
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(choices):
        return choices[int(ans) - 1]
    if ans in choices:
        return ans
    print("无效选择，已取消。")
    return None


def _ask_action(obj: str) -> str | None:
    """对象已知、缺动作时，交互询问动作。"""
    print(f"对象 {obj!r} 支持的动作：")
    return _pick("请选择动作（输入编号或名称，Enter=取消）: ", ACTIONS_BY_OBJECT[obj])


def _ask_object(action: str) -> str | None:
    """动作已知、缺对象时，交互询问对象。"""
    print(f"动作 {action!r} 可用于的对象：")
    return _pick("请选择对象（输入编号或名称，Enter=取消）: ", ACTION_OBJECTS[action])


def _fill_missing_object(argv: list[str]) -> list[str]:
    """第一个位置词若是「动作」（而非对象），交互补全对象并插到其前面。

    在 argparse 解析前调用：第一级子命令只接受 role/work/author，
    直接输 add/list 等会被判 invalid choice，需先补出对象。
    """
    skip_value = False
    for i, a in enumerate(argv):
        if skip_value:
            skip_value = False
            continue
        if a == "--kb":
            skip_value = True  # --kb 后跟一个取值，跳过
            continue
        if a.startswith("-"):
            continue
        if a in ACTION_OBJECTS and a not in OBJECTS:
            obj = _ask_object(a)
            if obj is None:
                return argv  # 取消补全，交回 argparse 报错
            return argv[:i] + [obj] + argv[i:]
        break
    return argv


# 业务实现已下沉到 lib/kb/cmds.py 与 lib/kb/authors.py，本文件仅保留命令行入口。


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--kb", metavar="DIR", default=str(KB_DEFAULT),
                        help=f"对照数据库目录（默认 {KB_DEFAULT}）")

    sub = parser.add_subparsers(dest="object", metavar="{role,work,author}")

    # 角色维护
    p_role = sub.add_parser("role", help="角色维护")
    role_sub = p_role.add_subparsers(dest="action",
                                     metavar="{add,del,list,check,merge,suggest,set-default}")
    role_sub.add_parser("add", help="添加角色")
    role_sub.add_parser("del", help="删除角色")
    role_sub.add_parser("list", help="列出角色")
    role_sub.add_parser("check", help="检查角色")
    role_sub.add_parser("merge", help="合并角色（候选确认）")
    role_sub.add_parser("suggest", help="疑似匹配建议")
    role_sub.add_parser("set-default", help="设定角色默认名")

    # 作品维护
    p_work = sub.add_parser("work", help="作品维护")
    work_sub = p_work.add_subparsers(dest="action",
                                     metavar="{add,del,list,check,merge,set-default,rename,edit}")
    work_sub.add_parser("add", help="添加作品")
    work_sub.add_parser("del", help="删除作品（连同角色）")
    work_sub.add_parser("list", help="列出作品")
    work_sub.add_parser("check", help="作品检查")
    work_sub.add_parser("merge", help="合并作品（候选确认）")
    work_sub.add_parser("set-default", help="设定作品默认名")
    work_sub.add_parser("edit", help="编辑作品（名称别名/排序/分类/作品键）")
    p_rename = work_sub.add_parser("rename", help="重命名作品键（OLD NEW；不给则交互选择）")
    p_rename.add_argument("keys", nargs="*", metavar="OLD_KEY NEW_KEY")
    p_rename.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run 预览）")

    # 作者维护
    p_author = sub.add_parser("author", help="作者维护")
    author_sub = p_author.add_subparsers(dest="action",
                                         metavar="{merge,sync,rebuild,check,alias,patch}")
    p_merge = author_sub.add_parser("merge", help="合并重复作者（候选逐对确认）")
    p_merge.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    p_sync = author_sub.add_parser("sync", help="从模型 .ysm 推导作者名并入 authors.json")
    p_sync.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    author_sub.add_parser("rebuild", help="重建集中作者数据 authors.json")
    author_sub.add_parser("check", help="只检查 authors.json 差异（不写盘）")
    author_sub.add_parser("alias", help="为作者添加别名（交互）")
    p_patch = author_sub.add_parser(
        "patch", help="从手动信息文件合并作者信息（平台/团队/别名）")
    p_patch.add_argument("file", metavar="FILE", help="手动维护的信息 JSON 文件")
    p_patch.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")

    # 缺对象时（第一个位置词是动作）交互补全对象，再解析
    args = parser.parse_args(_fill_missing_object(sys.argv[1:]))

    kb_path = Path(args.kb)
    if not kb_path.is_absolute():
        kb_path = REPO_ROOT / kb_path

    obj = args.object
    act = getattr(args, "action", None)

    # role 不带动作时直接进入统一角色管理菜单（增删改查/别名/默认名/皮肤）
    if obj == "role" and act is None:
        return roles_cmd(kb_path)

    # 有对象但缺动作：交互补全动作
    if obj is not None and act is None:
        act = _ask_action(obj)

    if obj is None or act is None:
        parser.print_help()
        return 2

    apply_flag = getattr(args, "apply", False)

    # 作者维护
    if obj == "author":
        if act == "merge":
            return merge_authors_flow(apply_flag)
        if act == "sync":
            return sync_authors_from_models(apply_flag)
        if act == "rebuild":
            return write_authors_data(check_only=False)
        if act == "check":
            return write_authors_data(check_only=True)
        if act == "alias":
            return add_author_alias()
        if act == "patch":
            return merge_author_info(Path(args.file), apply_flag)

    # 角色维护
    if obj == "role":
        if act == "add":
            return add_role_cmd(kb_path)
        if act == "del":
            return del_role_cmd(kb_path)
        if act == "list":
            return list_role_cmd(kb_path)
        if act == "check":
            run_check(kb_path)
            return 0
        if act == "merge":
            run_merge(kb_path)
            return 0
        if act == "suggest":
            run_suggest(kb_path)
            return 0
        if act == "set-default":
            return set_default_role_cmd(kb_path)

    # 作品维护
    if obj == "work":
        if act == "add":
            add_work_interactive(kb_path)
            return 0
        if act == "del":
            del_works_cmd(kb_path)
            return 0
        if act == "list":
            list_works_cmd(kb_path)
            return 0
        if act == "check":
            check_works_cmd(kb_path)
            return 0
        if act == "merge":
            merge_works_cmd(kb_path)
            return 0
        if act == "set-default":
            set_default_work_cmd(kb_path)
            return 0
        if act == "edit":
            edit_work_cmd(kb_path)
            return 0
        if act == "rename":
            keys = args.keys
            if len(keys) == 2:
                return rename_work_cmd(kb_path, keys[0], keys[1],
                                       apply_changes=apply_flag)
            if len(keys) == 0:
                return rename_work_interactive(kb_path, apply_changes=apply_flag)
            print("错误: rename 需要 0 或 2 个参数（交互选择 / OLD NEW）。",
                  file=sys.stderr)
            return 2

    print(f"未知操作: {obj} {act}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
