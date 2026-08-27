# -*- coding: utf-8 -*-
"""kb 作品/角色共享函数：works 别名索引与角色条目的通用工具。

（原 resolve_name / build_kb 旧解析器已删除——命名统一由 parse2.resolve_name3
纯三段转换处理，本模块只保留跨模块共享的辅助函数。）
"""
from __future__ import annotations

from lib.kb.text import normalize_work_name

# 运行时由 works 数据（含 README 同步）派生的作品名 -> 键 映射（去标点归一化后）
EXTRA_WORK_ALIASES: dict[str, str] = {}


def set_work_aliases(aliases: dict[str, str]) -> None:
    """写入作品名 -> 键 映射（由 sync.build_work_index 调用，替代跨模块改全局）。"""
    EXTRA_WORK_ALIASES.clear()
    EXTRA_WORK_ALIASES.update(aliases)


def get_work_canonical(seg: str) -> str | None:
    """作品名 -> 规范键（完全依赖外置 works 数据构建的 EXTRA_WORK_ALIASES）。"""
    return EXTRA_WORK_ALIASES.get(normalize_work_name(seg))


def role_names(r: dict, field: str) -> list[str]:
    """取角色条目某字段的名称列表（字符串 -> 单元素列表；数组去空保序）。"""
    v = r.get(field)
    if isinstance(v, list):
        out = []
        for x in v:
            if x and x not in out:
                out.append(x)
        return out
    return [v] if v else []


def role_key(r: dict) -> str:
    """角色条目的去重键：取 zh/en 的规范名（数组第一个）。"""
    cn = role_names(r, "zh")
    en = role_names(r, "en")
    cn_main = cn[0] if cn else ""
    en_main = en[0] if en else ""
    return f"{r['work']}|{cn_main}|{en_main.lower()}"
