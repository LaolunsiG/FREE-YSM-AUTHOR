# -*- coding: utf-8 -*-
"""角色术语规范化：把 .ysm 作者块的原始 Role 值（如 "Model author" / "动画" / "動作"）
归一为标准术语（中英双语标签），统一同一内容的不同表达。

数据：author-info/role_terms.json（{terms: [{key, cn, en, aliases}]}）。
用法：
  normalize_role("Model author, 动画") -> "#模型 #动画 | #Model #Animation"
"""
from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths  # noqa: E402

# 角色片段分隔符（逗号/顿号/分号/斜杠/空格）
_ROLE_SPLIT_RE = re.compile(r'[,，、;；/|]+')


def load_role_terms() -> dict:
    """读取角色术语表（author-info/role_terms.json）；缺失返回空 terms。"""
    data = lib_paths.load_json(lib_paths.data_path('author-info', 'role_terms.json'), {})
    return data.get('terms') if isinstance(data, dict) else []


def _fold(text: str) -> str:
    """术语匹配用归一化：NFKC、去空白与标点、小写（保持中文原样）。"""
    text = unicodedata.normalize('NFKC', text)
    return re.sub(r'[\s\u00a0\u200b\-_.]+', '', text).lower()


def match_term(part: str, terms: list[dict]) -> dict | None:
    """把单个角色片段匹配到术语表条目；未命中返回 None。

    匹配规则：片段与任一 alias 归一化后相等，或互为子串（短别名容易误伤，
    因此片段/别名长度 >= 2 才做子串匹配）。返回第一个命中项。
    """
    folded = _fold(part)
    if not folded:
        return None
    for term in terms:
        for alias in term.get('aliases', []):
            a = _fold(alias)
            if not a:
                continue
            if a == folded:
                return term
            if len(a) >= 2 and len(folded) >= 2 and (a in folded or folded in a):
                return term
    return None


def normalize_role(role: str, terms: list[dict] | None = None) -> str:
    """把原始 Role 字符串归一为标准术语标签（中英双语）。

    - 已是标签格式（含 # 或 |，如作者 README 的 Role）原样返回；
    - 否则按分隔符拆分，逐段匹配术语表，输出 "#cn1 #cn2 | #en1 #en2"；
    - 未匹配的片段保留原文（去 # 后并入中文侧）。
    """
    role = (role or '').strip()
    if not role:
        return ''
    if '#' in role or '|' in role:
        return role
    if terms is None:
        terms = load_role_terms()

    cn_tags: list[str] = []
    en_tags: list[str] = []
    for raw in _ROLE_SPLIT_RE.split(role):
        part = raw.strip()
        if not part:
            continue
        term = match_term(part, terms)
        if term:
            cn, en = term.get('zh'), term.get('en')
            if cn and cn not in cn_tags:
                cn_tags.append(cn)
            if en and en not in en_tags:
                en_tags.append(en)
        elif part.lstrip('#＃') not in cn_tags:
            cn_tags.append(part.lstrip('#＃'))
    if not cn_tags:
        return ''
    out = '#' + ' #'.join(cn_tags)
    if en_tags:
        out += ' | #' + ' #'.join(en_tags)
    return out
