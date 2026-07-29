"""
敏感信息脱敏模块。

在日志送入 LLM 之前，用纯正则规则脱敏敏感字段。
两遍处理：① KV 模式（key=value → key=[REDACTED]）② 独立模式（邮箱/IP/手机/JWT…）
无外部依赖，参考 logai processor.go:MaskSensitiveInfo。
"""

import re

# =============================================================================
# 第一遍：Key-Value 模式（保留 key 名，替换 value 为类型标签）
# =============================================================================

_SENSITIVE_KV_PATTERNS: list[tuple[re.Pattern, str]] = [
    # (匹配整个 "key[:=]\s*["']?value["']?" 的正则, 替换模板)
    # 替换模板中 ${1}=key前缀, ${2}=分隔符, ${3}=引号, ${4}=闭合引号
    (re.compile(r'(?i)(password|passwd)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[REDACTED]'),
    (re.compile(r'(?i)(token|access_token|refresh_token)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[TOKEN]'),
    (re.compile(r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[API_KEY]'),
    (re.compile(r'(?i)(secret|client_secret|app_secret)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[SECRET]'),
    (re.compile(r'(?i)(username|user)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[USER]'),
    (re.compile(r'(?i)(email|mail)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[EMAIL]'),
    (re.compile(r'(?i)(phone|telephone|mobile)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[PHONE]'),
    (re.compile(r'(?i)(credit[_\s]?card|creditcard)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[CREDIT_CARD]'),
    (re.compile(r'(?i)(ssn|social)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[SSN]'),
    (re.compile(r'(?i)(address)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[ADDRESS]'),
    (re.compile(r'(?i)(ip|ip_address)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[IP]'),
    (re.compile(r'(?i)(mac|mac_address)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[MAC]'),
    (re.compile(r'(?i)(private[_\s]?key|public[_\s]?key)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[KEY]'),
    (re.compile(r'(?i)(certificate|cert)\s*[:=]\s*["\']?[^"\'&\s]+["\']?'),
     r'\1=[CERTIFICATE]'),
    (re.compile(r'(?i)(authorization|auth)\s*[:=]\s*["\']?(?:Bearer\s+)?([^"\'&\s]+)["\']?'),
     r'\1=Bearer [TOKEN]'),
]

# =============================================================================
# 第二遍：独立模式（直接替换整个匹配为类型标签）
# =============================================================================

_EMAIL_RE = re.compile(r'[\w\.\-+]+@[\w\.\-]+\.\w+')
_PHONE_CN_RE = re.compile(r'\b1[3-9]\d{9}\b')
_IP_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
_MAC_RE = re.compile(r'([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})')
_JWT_RE = re.compile(r'eyJ[A-Za-z0-9\-_]*\.[A-Za-z0-9\-_]*\.[A-Za-z0-9\-_]*')
_ID_CARD_RE = re.compile(
    r'\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b'
    r'|\b[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}\b'
)
_CREDIT_CARD_RE = re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b')
_URL_PARAM_RE = re.compile(r'([&?](?:password|token|key|secret)=)[^&\s]+', re.IGNORECASE)


def sanitize(content: str) -> str:
    """
    对日志内容进行敏感信息脱敏。

    两遍处理：
    1. KV 模式：password=xxx → password=[REDACTED]
    2. 独立模式：正则直接匹配邮箱/IP/手机号/JWT/身份证/信用卡/URL参数
    """
    if not content:
        return content

    # ---- 第一遍：KV 模式 ----
    for pattern, replacement in _SENSITIVE_KV_PATTERNS:
        content = pattern.sub(replacement, content)

    # ---- 第二遍：独立模式 ----
    content = _EMAIL_RE.sub('[EMAIL]', content)
    content = _PHONE_CN_RE.sub('[PHONE]', content)
    content = _IP_RE.sub('[IP]', content)
    content = _MAC_RE.sub('[MAC]', content)
    content = _JWT_RE.sub('[JWT_TOKEN]', content)
    content = _ID_CARD_RE.sub('[ID_NUMBER]', content)
    content = _CREDIT_CARD_RE.sub('[CREDIT_CARD]', content)
    content = _URL_PARAM_RE.sub(r'\1[REDACTED]', content)

    return content
