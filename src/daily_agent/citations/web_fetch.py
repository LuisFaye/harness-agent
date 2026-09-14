"""在严格网络边界内读取候选网页并提取可核对正文。"""

from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx

from daily_agent.citations.registry import canonicalize_web_url


MAX_RESPONSE_BYTES = 1_500_000
MAX_REDIRECTS = 3
_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"


def fetch_page_text(url: str, *, deadline: float) -> str | None:
    """读取单个候选网页；任何安全或解析失败都按弱证据降级。

    每次跳转前都重新校验目标主机和 DNS 解析结果，避免公开 URL 通过 30x
    重定向到内网。响应采用流式限长读取，防止异常页面消耗过多内存。调用方
    传入整条引用流水线的绝对 deadline，保证网页慢响应不会拖住最终回答。
    """
    current_url = canonicalize_web_url(url)
    if not current_url:
        return None
    for _ in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0.2 or not _public_http_target(current_url):
            return None
        timeout = httpx.Timeout(min(3.0, remaining), connect=min(1.5, remaining))
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream(
                    "GET",
                    current_url,
                    headers={
                        "User-Agent": "SB-Agent-Citation/1.0",
                        "Accept": "text/html,text/plain;q=0.9",
                    },
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        current_url = canonicalize_web_url(urljoin(current_url, location or ""))
                        if not current_url:
                            return None
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(item in content_type for item in _ALLOWED_CONTENT_TYPES):
                        return None
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            return None
                    encoding = response.encoding or "utf-8"
                    decoded = bytes(data).decode(encoding, errors="replace")
                    if "text/plain" in content_type:
                        return _normalize_document_text(decoded)
                    parser = _VisibleTextParser()
                    parser.feed(decoded)
                    return _normalize_document_text(parser.text())
        except (httpx.HTTPError, UnicodeError, ValueError, OSError):
            return None
    return None


def best_passage(document_text: str, answer_text: str, snippet: str = "") -> tuple[str, int, int] | None:
    """用可解释的词项覆盖率，从真实网页文本中选择最相关原文片段。

    模型只负责挑候选来源；升级为 passage 必须经过这里的确定性检查。评分
    重点比较回答块与网页段落的共同词项，搜索摘要只用于并列结果的辅助排序，
    不会仅凭摘要相似度把无关正文升级成强证据。
    """
    answer_terms = _terms(answer_text)
    if len(answer_terms) < 2:
        return None
    snippet_terms = _terms(snippet)
    candidates = _passage_candidates(document_text)
    best: tuple[float, str, int, int] | None = None
    for text, start, end in candidates:
        passage_terms = _terms(text)
        if not passage_terms:
            continue
        common = answer_terms & passage_terms
        coverage = len(common) / max(1, min(len(answer_terms), 24))
        snippet_overlap = len(snippet_terms & passage_terms) / max(1, min(len(snippet_terms), 24))
        score = coverage + snippet_overlap * 0.2
        if len(common) < 2 or coverage < 0.12:
            continue
        if best is None or score > best[0]:
            best = (score, text, start, end)
    return (best[1], best[2], best[3]) if best else None


def _public_http_target(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError:
        return False
    if not addresses:
        return False
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            return False
        parsed_addresses.append(ip)
    if all(ip.is_global for ip in parsed_addresses):
        return True
    # Clash 等 TUN 的 Fake-IP 模式会把所有公网域名映射到 198.18.0.0/15。
    # 直接放行该保留网段会削弱 SSRF 防护，因此仅在本地结果全部属于 Fake-IP
    # 时，通过固定的公共 DoH 服务取得权威 A/AAAA 记录，再按公网地址规则复核。
    if parsed_addresses and all(ip in _FAKE_IP_NETWORK for ip in parsed_addresses):
        verified_addresses = _resolve_with_doh(hostname)
        return bool(verified_addresses) and all(ip.is_global for ip in verified_addresses)
    return False


def _resolve_with_doh(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    verified: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for record_type in ("A", "AAAA"):
        try:
            response = httpx.get(
                _DOH_ENDPOINT,
                params={"name": hostname, "type": record_type},
                headers={"Accept": "application/dns-json"},
                timeout=1.5,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue
        answers = payload.get("Answer") if isinstance(payload, dict) else None
        if not isinstance(answers, list):
            continue
        expected_type = 1 if record_type == "A" else 28
        for answer in answers:
            if not isinstance(answer, dict) or answer.get("type") != expected_type:
                continue
            try:
                verified.append(ipaddress.ip_address(str(answer.get("data") or "")))
            except ValueError:
                continue
    return verified


class _VisibleTextParser(HTMLParser):
    _SKIPPED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS = {"p", "article", "section", "main", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def _normalize_document_text(value: str) -> str:
    lines = [re.sub(r"[ \t\f\v]+", " ", item).strip() for item in str(value or "").splitlines()]
    return "\n".join(item for item in lines if item)[:500_000]


def _passage_candidates(document_text: str) -> list[tuple[str, int, int]]:
    candidates: list[tuple[str, int, int]] = []
    for match in re.finditer(r"[^\n]{40,1200}", document_text):
        text = re.sub(r"\s+", " ", match.group(0)).strip()
        if 40 <= len(text) <= 1200:
            candidates.append((text, match.start(), match.end()))
    return candidates[:3000]


def _terms(value: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(value or "").lower())
    latin = set(re.findall(r"[a-z0-9][a-z0-9._-]{1,}", normalized))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    chinese = {run[index:index + 2] for run in chinese_runs for index in range(len(run) - 1)}
    return latin | chinese
