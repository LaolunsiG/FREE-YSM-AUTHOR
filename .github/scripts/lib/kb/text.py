# -*- coding: utf-8 -*-
"""kb 名称字符串工具：CJK 检测、英文名规范化、作品名规范化、皮肤标签（无 lib 依赖）。"""
import re

# 中文字符 + 日文假名（平假名/片假名）：知识库角色名可含日文写法（如 長崎そよ）。
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
TOUHOU_PREFIX_RE = re.compile(r"(?i)^touhou(.+)$")
MIXED_SEG_RE = re.compile(
    r"^(?P<cn>[\u4e00-\u9fff·]+)(?:-(?P<skin>[\u4e00-\u9fff·][\u4e00-\u9fff·-]*))?(?:-|_|\s+)(?P<en>.+)$"
)
CN_SKIN_RE = re.compile(r"^(.+?)-([\u4e00-\u9fff].*)$")
EN_TAIL_RE = re.compile(r"[-_][^-_]+$")
PAREN_RE = re.compile(r"[\(（][^\)）]*[\)）]")


def has_cjk(s: str) -> bool:
    """是否包含中文字符。"""
    return CJK_RE.search(s) is not None


def init_caps(s: str) -> str:
    """全小写 token 首字母大写；已含大写的 token 不动。"""
    if not s:
        return s

    def repl(m: re.Match) -> str:
        sep, t = m.group(1), m.group(2)
        return sep + t[0].upper() + t[1:]

    # 分隔符（串首 / 空白 / - _ （ (）后的全小写 token -> 首字母大写
    return re.sub(r"(^|[\s_\-（(])([a-z][a-z0-9]*)", repl, s)


def normalize_en_key(s: str) -> str:
    """英文名归一化：去括号内容、去空白、小写。"""
    t = PAREN_RE.sub("", s)
    t = re.sub(r"\s+", "", t)
    return t.lower()


def normalize_work_name(name: str) -> str:
    """作品名归一化：小写、去标点（保留中文字符与字母数字）。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", name.lower())



