# -*- coding: utf-8 -*-
"""交互输入工具：rename_model_folders / audit_models 等交互式命令共用的安全输入。

统一"Ctrl+C / stdin 耗尽视为退出(q)"的行为，避免各脚本各自实现。
"""


def ask(prompt: str) -> str:
    """安全的交互输入：去 BOM、去首尾空白；非交互 stdin 耗尽或 Ctrl+C 时返回 'q'（退出）。

    返回 'q' 后各交互命令会保存已完成的部分并优雅退出（与显式输入 q 等价），
    避免用户在确认环节按 Ctrl+C 时直接抛 KeyboardInterrupt 崩溃。
    """
    try:
        return input(prompt).strip().lstrip('\ufeff')
    except (EOFError, KeyboardInterrupt):
        return 'q'
