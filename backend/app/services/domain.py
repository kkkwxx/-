"""域名归一：注册域（eTLD+1，小写）。

不引入 tldextract / publicsuffix2 —— 红线只允许 3 个新增库级组件且已用满（技术栈 §0.1）。
这里用"多级公共后缀表"实现，覆盖项目真实会遇到的域名；未收录的多级后缀会在
`tests/test_domain.py::test_all_seed_domains_stable` 里暴露出来，靠加表项解决。
"""

from __future__ import annotations

from urllib.parse import urlsplit

# 常见的两级公共后缀（第二级 + 注册局级）。命中则注册域取三级。
MULTI_LEVEL_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "sch.uk",
        "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn", "mil.cn",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "co.nz", "net.nz", "org.nz",
        "co.kr", "or.kr", "ne.kr",
        "com.tw", "org.tw", "net.tw",
        "com.hk", "org.hk", "net.hk", "edu.hk", "gov.hk",
        "com.sg", "edu.sg", "gov.sg",
        "co.in", "net.in", "org.in", "ac.in", "gov.in",
        "com.br", "net.br", "org.br", "gov.br",
        "com.mx", "org.mx", "gob.mx",
        "co.za", "org.za",
        "com.tr", "org.tr", "gov.tr",
        "com.ru", "org.ru", "net.ru",
        "co.il", "org.il", "ac.il", "gov.il",
        "com.es", "org.es", "gob.es",
        "com.ar", "com.co", "com.pe", "com.ve",
        "co.th", "or.th", "ac.th",
        "com.my", "com.ph", "com.vn", "com.pk", "com.eg", "com.sa", "com.ua",
        "edu.cn",
    }
)

# 明确不需要做归一的位置：裸 IP 直接返回
_MAX_HOST_LEN = 253


def _strip_port(host: str) -> str:
    if host.startswith("["):  # IPv6
        return host.split("]")[0].lstrip("[")
    return host.split(":")[0]


def normalize_host(raw: str | None) -> str | None:
    """把任意 URL / 裸域名转成小写 host。取不到返回 None。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if "//" not in text:
        text = "//" + text
    try:
        parts = urlsplit(text if text.startswith(("http", "//")) else "//" + text)
        host = parts.hostname or ""
    except ValueError:
        return None
    host = _strip_port(host.strip().rstrip(".").lower())
    if not host or len(host) > _MAX_HOST_LEN:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass
    return host or None


def registrable_domain(raw: str | None) -> str | None:
    """返回 eTLD+1 小写。例：news.bbc.co.uk → bbc.co.uk；www.nasa.gov → nasa.gov。"""
    host = normalize_host(raw)
    if not host:
        return None
    # 裸 IP（v4 / v6）不做后缀处理
    if host.replace(".", "").isdigit() or ":" in host:
        return host
    labels = host.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return None
    last_two = ".".join(labels[-2:])
    if last_two in MULTI_LEVEL_SUFFIXES:
        if len(labels) < 3:
            return None  # 只有后缀本身，不是合法注册域
        return ".".join(labels[-3:])
    return last_two


def is_valid_registrable_domain(raw: str) -> bool:
    domain = registrable_domain(raw)
    if not domain:
        return False
    return "." in domain and not domain.startswith(".")
