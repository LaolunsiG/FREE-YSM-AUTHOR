#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YSM 模型文件夹批量重命名工具（本仓库专用）。

用法（按功能分组）:

  预览与重命名（纯重命名，不收录数据库）:
    python '.github/scripts/models_organize/02_rename_model_folders.py'                        # 预览，默认每一个作者的文件夹
    --apply                # 执行重命名（自动处理无争议条目）
    Models/0001 --apply    # 只处理某作者目录
    Models/0001/模型名      # 只处理单个模型目录

  预览显示过滤（控制台与报告一致；默认只显示已修改 fix）:
    --show ok               # 只显示已规范
    --show fix,ok            # 显示已修改 + 已规范
    --show-kb --show-fix     # 显示知识库补全修复 / 已修改
    --show-skip              # 只显示跳过（含问题）
    --show-all               # 显示全部条目

  CSV 批处理模式（推荐）:
    --export-plan plan.csv   # 导出所有条目到 CSV
    --import-plan plan.csv   # 预览导入计划
    --import-plan plan.csv --apply  # 执行导入计划

  回滚:
    --undo                   # 回滚最近一次操作
    --undo --log-file log.json  # 回滚指定日志
    --undo --dry-run        # 预览回滚

详细匹配规则与维护说明请参考 02_rename_model_folders.md。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime

# 添加 lib 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths
from lib.kb.cmds import build_indexes, get_target_dirs
from lib.kb.parse2 import resolve_name3
from lib.kb.storage import load_kb_json, migrate_from_sqlite, add_role_to_work
from lib.kb.sync import build_work_index
from lib.csv_plan import export_plan, import_plan
from lib.rollback import backup_kb_dir, write_operation_log, load_log, rollback

REPO_ROOT = lib_paths.WORKSPACE_ROOT
KB_DEFAULT = lib_paths.MODEL_INFO_DIR
LOG_DIR = REPO_ROOT / ".github" / "scripts" / "logs"  # 日志存放目录

# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    g_run = parser.add_argument_group("预览与重命名")
    g_run.add_argument("--apply", action="store_true",
                       help="直接执行重命名（自动处理无争议条目，跳过冲突/未知）")
    g_run.add_argument("--kb", metavar="DIR", default=str(KB_DEFAULT),
                       help=f"对照数据库目录（默认 {KB_DEFAULT}）")
    g_run.add_argument("--report", metavar="FILE", default="",
                       help="报告输出路径（默认写入系统临时目录）")

    g_show = parser.add_argument_group("预览显示过滤")
    g_show.add_argument("--show", metavar="STATUS[,STATUS...]", action="append", default=None,
                        help="精确指定显示哪些状态的条目，可多次或逗号分隔（ok/fix/skip）")
    g_show.add_argument("--show-kb", action="store_true", help="显示知识库补全修复条目（fix）")
    g_show.add_argument("--show-fix", action="store_true", help="显示已修改条目（fix）")
    g_show.add_argument("--show-skip", action="store_true", help="显示跳过条目（skip，含问题）")
    g_show.add_argument("--show-ok", action="store_true", help="显示已规范条目（ok）")
    g_show.add_argument("--show-all", action="store_true",
                        help="显示全部条目（等价于 --show ok,fix,skip）")

    # CSV 批处理
    g_csv = parser.add_argument_group("CSV 批处理")
    g_csv.add_argument("--export-plan", metavar="CSV", default="",
                       help="导出计划到 CSV 文件")
    g_csv.add_argument("--import-plan", metavar="CSV", default="",
                       help="导入 CSV 计划（可配合 --apply 执行）")

    # 回滚
    g_undo = parser.add_argument_group("回滚")
    g_undo.add_argument("--undo", action="store_true", help="回滚最近一次操作")
    g_undo.add_argument("--log-file", metavar="FILE", default="",
                        help="指定回滚的日志文件（不指定则使用最新）")
    g_undo.add_argument("--dry-run", action="store_true",
                        help="预览回滚操作但不实际执行")

    parser.add_argument('paths', nargs='*', default=None,
                        help='直接引用路径处理（可多个，如 Models/0001 或 Models/0001/模型名；'
                             '不传则默认 Models + Other-YSM-Models）')
    args = parser.parse_args()

    kb_path = Path(args.kb)
    if not kb_path.is_absolute():
        kb_path = REPO_ROOT / kb_path

    # ---- 回滚模式 ----
    if args.undo:
        log_file = None
        if args.log_file:
            log_file = Path(args.log_file)
        else:
            # 查找最新日志
            log_dir = LOG_DIR
            if log_dir.exists():
                logs = sorted(log_dir.glob("rename_log_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if logs:
                    log_file = logs[0]
        if not log_file or not log_file.exists():
            print("未找到可回滚的日志文件。")
            return 1
        print(f"将回滚日志: {log_file}")
        if args.dry_run:
            print("预览模式（未实际执行）")
        success = rollback(log_file, dry_run=args.dry_run)
        return 0 if success else 1

    # ---- 加载知识库 ----
    data = load_kb_json(kb_path)
    if not data.get("roles"):
        m, _ = migrate_from_sqlite(kb_path, kb_path / "ysm_kb.db" if kb_path.is_dir()
                                   else kb_path.with_suffix(".db"))
        if m:
            data["roles"] = list(m)
    build_work_index(data)
    roles = list(data.get("roles") or [])
    print(f"知识库: {len(roles)} 条")

    _cn_idx, _en_idx, en_to_cn, cn_to_en = build_indexes(roles)

    # ---- 收集目标目录 ----
    dirs: list[Path] = []
    for p in (args.paths or [None]):
        sub = get_target_dirs(p)
        if not sub and p:
            pp = Path(p).resolve()
            if pp.is_dir():
                dirs.append(pp)
        else:
            dirs.extend(sub)
    dirs = sorted(set(dirs), key=lambda d: str(d))
    if not dirs:
        print("未找到任何目标文件夹。", file=sys.stderr)
        return 2
    print(f"共找到 {len(dirs)} 个待处理文件夹")

    # ---- 解析所有文件夹 ----
    results = []
    for d in dirs:
        res = resolve_name3(d.name, roles, en_to_cn, cn_to_en)
        res["path"] = d
        res["parent_relative"] = d.parent.relative_to(REPO_ROOT).as_posix()
        results.append(res)

    # ---- CSV 导出 ----
    if args.export_plan:
        csv_path = Path(args.export_plan)
        export_plan(results, csv_path)
        print(f"计划已导出至: {csv_path}")
        return 0

    # ---- CSV 导入 ----
    if args.import_plan:
        csv_path = Path(args.import_plan)
        if not csv_path.exists():
            print(f"CSV 文件不存在: {csv_path}")
            return 1
        print(f"导入计划: {csv_path}")
        rename_ops, kb_ops = import_plan(csv_path, kb_path, results, apply=args.apply, repo_root=REPO_ROOT)
        if args.apply:
            # 记录操作日志
            log_ops = []
            # 记录 KB 操作（先备份）
            backup_path = None
            if kb_ops:
                backups_dir = LOG_DIR / "backups"
                backup_path = backup_kb_dir(kb_path, backups_dir)
                if backup_path:
                    log_ops.append({
                        "type": "modify_kb",
                        "file": str(kb_path / "character"),
                        "backup_path": str(backup_path)
                    })
            # 记录重命名操作
            for op in rename_ops:
                log_ops.append({
                    "type": "rename_folder",
                    "old_path": str(op["old_path"]),
                    "new_path": str(op["new_path"])
                })
            if log_ops:
                log_file = write_operation_log(log_ops, " ".join(sys.argv), LOG_DIR)
                print(f"操作日志已保存: {log_file}")
        else:
            print("预览模式结束，加 --apply 执行。")
        return 0

    # ---- 原有预览/重命名模式（兼容） ----
    # 定义分类与显示（与原逻辑一致）
    ALL_TAGS = ("ok", "fix", "skip")
    TAG_LABELS = {"ok": "已规范", "fix": "已修改", "skip": "跳过"}
    PROBLEM_ORDER = ("conflict", "works", "cn-name", "en-name", "other")
    PROBLEM_LABELS = {"works": "缺作品", "cn-name": "缺中文名", "en-name": "缺英文名",
                      "conflict": "跨作品同名", "other": "其他歧义"}
    counts = {t: 0 for t in ALL_TAGS}
    problem_counts = {p: 0 for p in PROBLEM_ORDER}

    def classify(r: dict) -> tuple[str, list[str]]:
        if r["status"] == "SKIP":
            return "skip", []
        probs = list(r.get("problems") or [])
        if r["new"] != r["original"]:
            return "fix", probs
        if probs:
            return "skip", probs
        return "ok", []

    # 显示过滤
    if args.show_all:
        visible = set(ALL_TAGS)
    else:
        explicit = bool(args.show or args.show_kb or args.show_fix
                        or args.show_skip or args.show_ok)
        visible = set() if explicit else {"fix"}
        for s in (args.show or []):
            for part in s.split(","):
                part = part.strip().lower()
                if part in ALL_TAGS:
                    visible.add(part)
        if args.show_kb or args.show_fix:
            visible.add("fix")
        if args.show_skip:
            visible.add("skip")
        if args.show_ok:
            visible.add("ok")

    grouped: dict[str, list[str]] = {t: [] for t in ALL_TAGS}
    for r in results:
        folder_name = r["path"].name
        tag, probs = classify(r)
        counts[tag] += 1
        if tag != "ok":
            for p in probs:
                if p in problem_counts:
                    problem_counts[p] += 1

        # 构建显示行（只显示文件夹名）
        if tag == "skip":
            line = f"[skip] {folder_name}  (跳过"
            if r["notes"]:
                line += " -- " + r["notes"]
            if probs:
                prob_str = ", ".join(PROBLEM_LABELS.get(p, p) for p in probs)
                line += f" -- 问题: {prob_str}"
            line += ")"
        else:
            line = f"[{tag}] {folder_name}  =>  {r['new']}"
            if r["notes"]:
                line += "   <-- " + r["notes"]
            if r.get("filled"):
                line += "   [补全: " + r["filled"] + "]"
            if probs:
                prob_str = ", ".join(PROBLEM_LABELS.get(p, p) for p in probs)
                line += f"   [遗留问题: {prob_str}]"
        cws = r.get("conflict_works") or []
        if cws:
            shown = []
            for wkey in cws:
                wk = (data.get("works") or {}).get(wkey) or {}
                zh = wk.get("zh") or wk.get("en") or []
                if isinstance(zh, str):
                    zh = [zh]
                name = str(zh[0]) if zh else wkey
                shown.append(f"{wkey} {name}".rstrip())
            line += "   [命中作品: " + ", ".join(shown) + "]"
        if tag in visible:
            grouped[tag].append(line)

    # 输出报告
    report_lines: list[str] = []
    parent_dirs = sorted(set(
        r["path"].parent.relative_to(REPO_ROOT).as_posix()
        for r in results
    ))
    for pd in parent_dirs:
        print(f"[目录] {pd}/")
        report_lines.append(f"[目录] {pd}/")
    for t in ALL_TAGS:
        lines = grouped[t]
        if not lines or t not in visible:
            continue
        head = f"== {TAG_LABELS[t]} =="
        print(head)
        report_lines.append(head)
        for line in lines:
            print(line)
            report_lines.append(line)
    print()
    print(f"汇总: ok={counts['ok']}  已修改={counts['fix']}  跳过={counts['skip']}")
    if any(problem_counts.values()):
        prob_str = "  ".join(f"{PROBLEM_LABELS[p]}={problem_counts[p]}"
                             for p in PROBLEM_ORDER if problem_counts[p])
        print(f"问题计数: {prob_str}")

    if args.report:
        report_path = Path(args.report)
    else:
        import tempfile
        report_path = Path(tempfile.gettempdir()) / (
            f"ysm-rename-report-{datetime.now():%Y%m%d-%H%M%S}.txt")
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    label = "完整报告" if args.show_all else "报告(当前过滤)"
    print(f"{label}: {report_path}（{len(report_lines)} 行）")

    # ---- 执行重命名（--apply） ----
    if args.apply:
        # 筛选可处理的条目：无 conflict 且 work 不是 Unknown（或者 work 为 Unknown 但已有明确 new 且非原样？）
        # 我们只处理 new != original 且没有 conflict 的条目（冲突跳过）
        processable = [r for r in results
                       if r["new"] != r["original"]
                       and not r.get("conflict")
                       and r["work"] != "Unknown"]  # 未知也跳过，因为无法确定作品
        if not processable:
            print("没有可自动处理的条目（冲突/未知条目请使用 CSV 批处理）。")
            return 0

        # 记录操作
        log_ops = []
        # 备份 KB（如果有知识库修改，但自动模式不会修改 KB，只重命名）
        # 自动模式不修改知识库，所以无需备份 KB
        for r in processable:
            old = r["path"]
            new = old.with_name(r["new"])
            # 检查冲突（同名）
            if new.exists() and not (new.name == old.name and os.path.normcase(str(new)) == os.path.normcase(str(old))):
                # 同名冲突，加副本序号
                m_grade = re.search(r"_(LA|LB|LC|LD)$", r["new"])
                base = r["new"][:m_grade.start()] if m_grade else r["new"]
                grade = m_grade.group(0) if m_grade else ""
                n = 1
                while True:
                    cand = old.with_name(f"{base}-{n}{grade}")
                    if cand == old:
                        break
                    if not cand.exists():
                        new = cand
                        break
                    n += 1
                if new == old:
                    print(f"[warn] 无法解决冲突，跳过: {old.name}")
                    continue
            # 执行重命名
            try:
                # 大小写修正处理
                if new.name != old.name and os.path.normcase(str(new)) == os.path.normcase(str(old)):
                    tmp = old.with_name(old.name + ".casefix_tmp")
                    old.rename(tmp)
                    tmp.rename(new)
                else:
                    old.rename(new)
                log_ops.append({
                    "type": "rename_folder",
                    "old_path": str(old),
                    "new_path": str(new)
                })
                print(f"  重命名: {old.name} -> {new.name}")
            except Exception as e:
                print(f"[warn] 重命名失败: {old.name} -> {new.name} ({e})")

        # 记录日志
        if log_ops:
            log_file = write_operation_log(log_ops, " ".join(sys.argv), LOG_DIR)
            print(f"操作日志已保存: {log_file}")

    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())