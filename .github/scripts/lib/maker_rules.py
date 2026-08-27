# -*- coding: utf-8 -*-
"""制作者判定规则：把"谁算模型制作者"的 Role 匹配规则从代码外置到数据文件。

数据：author-info/maker_rules.json（{categories: {类键: {label, match, level, markers}}}）。

与 role_terms.json（README 展示用术语）职责分离——本文件只负责**归档归属判定**：
  - oc（形象来源/版权/设定）→ level 0：永不作为模型制作者，即使 Role 含"模型"
  - model（显式模型/建模）→ level 1：最强制作者信号
  - all（全包：模型+动画+物理）→ level 2：中级信号
  - author（作者自述）→ level 3：弱信号

匹配方式（match 字段）：
  - substr: 任一 marker 是 role 的子串
  - exact:  role 精确等于任一 marker
  - prefix: role 以任一 marker 开头（额外 prefixes 字段）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 .github/scripts 加回 sys.path，保证 lib/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import paths as lib_paths  # noqa: E402

# 数据文件缺失/损坏时的内置兜底（与旧版 ysm.py 硬编码等价的默认规则）
_DEFAULT_CATEGORIES: dict = {
    'oc': {
        'label': {'zh': '形象来源', 'en': 'Image Source'},
        'match': 'substr', 'level': 0,
        'markers': ['OC', '原型', '原IP', 'IP', '版权', '形象', '立绘', '原画',
                    '角色', '吉祥物', '人设', '设主', '单主', '系列二创', '原始模型指向'],
    },
    'model': {
        'label': {'zh': '模型作者', 'en': 'Model Author'},
        'match': 'substr', 'level': 1,
        'markers': ['模型', '建模', 'Model', 'model'],
    },
    'all': {
        'label': {'zh': '全包作者', 'en': 'All-round Author'},
        'match': 'substr', 'level': 2,
        'markers': ['全部', 'ALL', 'All', 'all', '都是我做', '全包', '全做', '制作工作'],
    },
    'author': {
        'label': {'zh': '作者', 'en': 'Author'},
        'match': 'exact', 'level': 3,
        'markers': ['作者', 'Author', 'author', '做者'],
        'prefixes': ['是作者'],
    },
}

# 模块级缓存：进程内只读一次磁盘
_CACHE: dict | None = None


def load_maker_rules() -> dict:
    """读取制作者判定规则（author-info/maker_rules.json）并缓存。

    数据缺失或格式异常（无 categories）时回退内置默认规则，保证归档/分类不崩。
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    data = lib_paths.load_json(lib_paths.data_path('author-info', 'maker_rules.json'), {})
    categories = data.get('categories') if isinstance(data, dict) else None
    _CACHE = categories if isinstance(categories, dict) and categories else _DEFAULT_CATEGORIES
    return _CACHE


def _match(rule: dict, role: str) -> bool:
    """按 rule 的 match 方式判断 role 是否命中该类别。"""
    if not rule:
        return False
    markers = rule.get('markers') or []
    if not markers:
        return False
    match = rule.get('match', 'substr')
    if match == 'exact':
        return role in markers
    if match == 'prefix':
        return any(role.startswith(m) for m in markers)
    # 默认 substr：任一 marker 是 role 的子串
    return any(m in role for m in markers)


def is_oc_role(role: str, categories: dict | None = None) -> bool:
    """role 是否为形象来源/版权/设定类（含"模型"也不算制作者）。"""
    if not role:
        return False
    cats = categories if categories is not None else load_maker_rules()
    return _match(cats.get('oc') or {}, role)


def role_level(role: str, categories: dict | None = None) -> int:
    """返回制作者信号强度：0=非制作者/形象来源，1=模型类，2=全包类，3=作者自述。

    - 先排除 oc（level 0）：即使 Role 含"模型"也不算制作者；
    - 再按 level 升序（1<2<3，信号从强到弱）逐类判断，取第一个命中；
    - 兜底逻辑保持与旧版 ysm.py 一致。
    """
    if not role:
        return 0
    cats = categories if categories is not None else load_maker_rules()
    if _match(cats.get('oc') or {}, role):
        return 0
    # 按 level 排序后依次判断（oc 已排除，剩余 level 均 > 0）
    ranked = sorted((c for c in cats.values() if c.get('level', 0) > 0),
                    key=lambda c: c.get('level', 0))
    for cat in ranked:
        if _match(cat, role) or _match_prefixes(cat, role):
            return int(cat.get('level', 0))
    return 0


def _match_prefixes(rule: dict, role: str) -> bool:
    """规则里额外的前缀标记（如"是作者"）命中判断。"""
    prefixes = rule.get('prefixes') or []
    return any(role.startswith(p) for p in prefixes)


def category_label(categories: dict | None = None,
                   lang: str = 'zh') -> dict[int, str]:
    """返回 {level: 标准称呼} 映射（如 {0: '形象来源', 1: '模型作者', ...}）。"""
    cats = categories if categories is not None else load_maker_rules()
    out: dict[int, str] = {}
    for cat in cats.values():
        label = (cat.get('label') or {}).get(lang)
        if label:
            out[int(cat.get('level', 0))] = label
    return out
