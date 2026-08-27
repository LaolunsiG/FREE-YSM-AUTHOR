#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回滚引擎：记录操作日志，支持撤销
"""

import json
import shutil
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional


def backup_kb_dir(kb_path: Path, backups_dir: Path) -> Path:
    """备份整个 knowledge base 的 character 目录"""
    char_dir = kb_path / "character"
    if not char_dir.exists():
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backups_dir / f"character_backup_{timestamp}.zip"
    shutil.make_archive(str(backup_file.with_suffix('')), 'zip', char_dir)
    return backup_file


def write_operation_log(operations: List[Dict], cmd: str, log_dir: Path) -> Path:
    """将操作列表写入日志文件，返回日志文件路径"""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"rename_log_{timestamp}.json"
    log_data = {
        "timestamp": timestamp,
        "command": cmd,
        "operations": operations
    }
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)
    return log_file


def load_log(log_file: Path) -> Dict:
    with open(log_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def rollback(log_file: Path, dry_run: bool = False) -> bool:
    """
    执行回滚：逆序处理 operations
    返回 True 表示成功，False 表示失败
    """
    log_data = load_log(log_file)
    operations = log_data.get("operations", [])
    if not operations:
        print("日志中没有操作记录。")
        return False

    # 逆序
    reversed_ops = list(reversed(operations))
    success = True

    for op in reversed_ops:
        op_type = op.get("type")
        if op_type == "rename_folder":
            old = Path(op["old_path"])
            new = Path(op["new_path"])
            if not new.exists():
                print(f"警告: 目标路径不存在，跳过回滚: {new}")
                continue
            if old.exists():
                print(f"警告: 原始路径已存在，跳过回滚以避免覆盖: {old}")
                continue
            if not dry_run:
                try:
                    new.rename(old)
                    print(f"  回滚重命名: {new.name} -> {old.name}")
                except Exception as e:
                    print(f"  回滚重命名失败: {e}")
                    success = False
            else:
                print(f"  (dry-run) 将重命名 {new} -> {old}")

        elif op_type == "modify_kb":
            file_path = Path(op["file"])
            backup_path = Path(op["backup_path"])
            if not backup_path.exists():
                print(f"警告: 备份文件不存在，跳过KB回滚: {backup_path}")
                continue
            if not dry_run:
                try:
                    shutil.copy2(backup_path, file_path)
                    print(f"  回滚KB文件: {file_path.name} (恢复自备份)")
                except Exception as e:
                    print(f"  回滚KB文件失败: {e}")
                    success = False
            else:
                print(f"  (dry-run) 将恢复 {file_path} 从 {backup_path}")

        else:
            print(f"未知操作类型: {op_type}")

    return success