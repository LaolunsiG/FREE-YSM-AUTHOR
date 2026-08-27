#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV 批处理计划管理模块
提供导出计划、导入计划功能
"""

import csv
import re
from pathlib import Path
from typing import List, Dict, Any

# 评级正则，用于剥离
GRADE_PATTERN = re.compile(r'_(LA|LB|LC|LD)$', re.IGNORECASE)


def export_plan(results: List[Dict], csv_path: Path) -> None:
    """
    将解析结果导出为 CSV 计划文件
    列：folder_name, parent_relative, current_status, problems, candidate_works,
        suggested_new_name, user_final_name, user_work, user_role_zh, user_role_en, notes
    """
    fieldnames = [
        "folder_name", "parent_relative", "current_status", "problems",
        "candidate_works", "suggested_new_name",
        "user_final_name", "user_work", "user_role_zh", "user_role_en",
        "notes"
    ]
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            # 提取信息
            folder_name = r["path"].name
            parent = r["path"].parent.as_posix()  # 相对路径由调用者处理，这里直接用绝对？为了通用，建议调用时传入相对
            # 由于结果中只存了 path，我们需要在调用时传入 repo_root 来计算相对路径，这里暂用绝对路径
            # 为了减少耦合，我们在主脚本中计算好 parent_relative 再传入。
            # 这里我们假设调用者已把 parent_relative 放入 r 中
            parent_relative = r.get("parent_relative", parent)
            status = r.get("status", "OK")
            problems = ",".join(r.get("problems", []))
            # candidate_works 仅冲突时有
            cws = r.get("conflict_works", [])
            candidate_works = ",".join(cws) if cws else ""
            suggested = r.get("new", folder_name)
            # 用户列留空
            row = {
                "folder_name": folder_name,
                "parent_relative": parent_relative,
                "current_status": status,
                "problems": problems,
                "candidate_works": candidate_works,
                "suggested_new_name": suggested,
                "user_final_name": "",
                "user_work": "",
                "user_role_zh": "",
                "user_role_en": "",
                "notes": r.get("notes", ""),
            }
            writer.writerow(row)


def import_plan(csv_path: Path, kb_path: Path, results: List[Dict],
                apply: bool = False, repo_root: Path = None) -> tuple[List[Dict], List[Dict]]:
    """
    导入 CSV 计划，返回需要执行的操作列表（重命名操作）和知识库更新操作（角色添加）
    如果 apply=True，则直接执行；否则只返回计划不执行。
    返回: (rename_ops, kb_ops)
    rename_ops: [{"old_path": Path, "new_path": Path, "folder_name": str}]
    kb_ops: [{"work_key": str, "role_zh": str, "role_en": str, "role_raw": str}]
    """
    # 读取 CSV
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("user_final_name", "").strip():
                rows.append(row)

    rename_ops = []
    kb_ops = []

    # 构建映射 folder_name -> result
    result_map = {r["path"].name: r for r in results}

    for row in rows:
        folder_name = row["folder_name"]
        if folder_name not in result_map:
            continue  # 跳过找不到的
        r = result_map[folder_name]

        # 确定最终新名
        user_final = row.get("user_final_name", "").strip()
        if not user_final:
            continue

        # 确定作品缩写
        user_work = row.get("user_work", "").strip()
        if not user_work:
            # 从建议名中解析（取第一个下划线前的部分）
            suggested = row.get("suggested_new_name", "")
            if suggested:
                user_work = suggested.split('_')[0]
            else:
                user_work = "Unknown"

        # 确定角色名（中/英）
        user_role_zh = row.get("user_role_zh", "").strip()
        user_role_en = row.get("user_role_en", "").strip()

        # 如果都为空，则从原始文件夹名去评级作为角色名
        raw_name = r["original"]  # 原始名
        raw_role = GRADE_PATTERN.sub('', raw_name)  # 去评级后缀

        # 构建知识库更新操作
        if user_role_zh or user_role_en:
            # 用户指定了
            if user_role_zh:
                kb_ops.append({"work_key": user_work, "role_name": user_role_zh, "source": "zh"})
            if user_role_en:
                kb_ops.append({"work_key": user_work, "role_name": user_role_en, "source": "en"})
        else:
            # 未指定，用去评级的原始名
            kb_ops.append({"work_key": user_work, "role_name": raw_role, "source": "raw"})

        # 重命名操作
        old_path = r["path"]
        new_path = old_path.with_name(user_final)
        if new_path != old_path:
            rename_ops.append({
                "old_path": old_path,
                "new_path": new_path,
                "folder_name": folder_name,
                "final_name": user_final,
                "result": r  # 保留原结果以便后续更新状态
            })

    # 如果 apply=True，则执行操作
    if apply:
        # 执行知识库更新
        from lib.kb.storage import add_role_to_work
        for op in kb_ops:
            added = add_role_to_work(op["role_name"], op["work_key"], kb_path)
            if added:
                print(f"  知识库新增: {op['work_key']} -> {op['role_name']}")
            else:
                print(f"  知识库已存在: {op['work_key']} -> {op['role_name']} (跳过)")

        # 执行重命名
        for op in rename_ops:
            try:
                op["old_path"].rename(op["new_path"])
                print(f"  重命名: {op['folder_name']} -> {op['final_name']}")
                # 更新 result 中的信息以便日志记录
                op["result"]["new"] = op["final_name"]
                op["result"]["status"] = "FIX"
            except Exception as e:
                print(f"  重命名失败: {op['folder_name']} -> {op['final_name']} ({e})")

    return rename_ops, kb_ops