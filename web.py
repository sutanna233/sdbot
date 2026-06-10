import re
import ipaddress
import socket
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup


DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

_BINARY_EXT = re.compile(
    r"\.(exe|zip|tar|gz|7z|rar|iso|bin|dll|so|dylib|apk|ipa|msi|dmg|pkg|deb|rpm|jar|war|class|pyc|wasm|mp3|mp4|avi|mov|mkv|flac|wav|ogg|webp|pdf|doc|docx|xls|xlsx|ppt|pptx)$",
    re.I,
)

_PRIV_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    seen = set()
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return True
    return False


def _check_url(url):
    if not url or not isinstance(url, str):
        return None, "url 不能为空"
    if "\x00" in url or "\n" in url or "\r" in url:
        return None, "url 含非法字符"
    if not re.match(r"^https?://", url, re.I):
        return None, "url 必须以 http:// 或 https:// 开头"
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        return None, f"scheme 不允许: {parsed.scheme}"
    if not parsed.netloc:
        return None, "url 缺少 host"
    host = parsed.hostname or ""
    if not host:
        return None, "url host 为空"
    if host.lower() in ("localhost", "0.0.0.0", "::", "[::]"):
        return None, f"内网 host 拒绝: {host}"
    if _is_private_host(host):
        return None, f"内网 IP 拒绝: {host}"
    if _BINARY_EXT.search(parsed.path):
        return None, f"二进制文件拒绝: {parsed.path}"
    return parsed, None


class WebFetcher:
    def __init__(self, config):
        wconf = config.get("web", {}) if config else {}
        self.timeout = int(wconf.get("timeout", 10))
        self.max_length = int(wconf.get("max_length", 50000))
        self.user_agent = wconf.get("user_agent", DEFAULT_UA)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    def fetch(self, url, format="text", max_length=None):
        max_len = int(max_length or self.max_length)
        parsed, err = _check_url(url)
        if err:
            return {"ok": False, "error": err, "url": url}

        try:
            resp = self._session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
                stream=True,
            )
        except requests.exceptions.SSLError as e:
            return {"ok": False, "error": f"SSL 错误: {e}", "url": url}
        except requests.exceptions.Timeout:
            return {"ok": False, "error": f"超时 {self.timeout}s", "url": url}
        except requests.exceptions.ConnectionError as e:
            return {"ok": False, "error": f"连接失败: {e}", "url": url}
        except Exception as e:
            return {"ok": False, "error": f"请求失败: {e}", "url": url}

        if len(resp.history) > 5:
            return {"ok": False, "error": f"重定向链过长: {len(resp.history)}", "url": url}
        for r in resp.history:
            p = urlparse(r.headers.get("Location", ""))
            if p.hostname and _is_private_host(p.hostname):
                return {"ok": False, "error": f"重定向到内网: {p.hostname}", "url": url}

        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "url": url}

        ctype = resp.headers.get("Content-Type", "").lower()
        if "text" not in ctype and "html" not in ctype and "xml" not in ctype and "json" not in ctype:
            return {"ok": False, "error": f"非文本内容: {ctype}", "url": url}

        try:
            resp.encoding = resp.apparent_encoding or "utf-8"
            content_bytes = b""
            for chunk in resp.iter_content(chunk_size=8192):
                content_bytes += chunk
                if len(content_bytes) > max_len * 3:
                    break
            raw = content_bytes.decode(resp.encoding, errors="replace")
        except Exception as e:
            return {"ok": False, "error": f"读取失败: {e}", "url": url}

        if len(raw) > max_len * 3:
            raw = raw[:max_len * 3]

        if format == "html":
            text = raw
        elif format == "markdown":
            text = self._to_markdown(raw, url)
        else:
            text = self._extract_text(raw)

        truncated = len(text) > max_len
        if truncated:
            text = text[:max_len]

        return {
            "ok": True,
            "url": url,
            "final_url": resp.url,
            "status": resp.status_code,
            "content_type": ctype,
            "encoding": resp.encoding,
            "title": self._extract_title(raw),
            "content": text,
            "length": len(text),
            "truncated": truncated,
        }

    def _extract_title(self, html):
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
        return ""

    def _extract_text(self, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return re.sub(r"<[^>]+>", " ", html)

        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas",
                         "header", "footer", "nav", "aside", "form", "button"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        return "\n".join(lines)

    def _to_markdown(self, html, url):
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return self._extract_text(html)

        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas",
                         "header", "footer", "nav", "aside", "form", "button"]):
            tag.decompose()

        lines = []
        title = soup.find("title")
        if title and title.string:
            lines.append(f"# {title.string.strip()}\n")

        main = soup.find("main") or soup.find("article") or soup.body or soup
        if main:
            for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "a", "code", "pre"]):
                tag_name = el.name
                txt = el.get_text(" ", strip=True)
                if not txt:
                    continue
                if tag_name == "h1":
                    lines.append(f"\n## {txt}\n")
                elif tag_name == "h2":
                    lines.append(f"\n### {txt}\n")
                elif tag_name == "h3":
                    lines.append(f"\n#### {txt}\n")
                elif tag_name == "h4":
                    lines.append(f"\n##### {txt}\n")
                elif tag_name == "li":
                    lines.append(f"- {txt}")
                elif tag_name == "p":
                    lines.append(f"{txt}\n")
                elif tag_name == "code":
                    lines.append(f"`{txt}`")
                elif tag_name == "pre":
                    lines.append(f"```\n{txt}\n```")
                elif tag_name == "a":
                    href = el.get("href", "")
                    if href and not href.startswith("#"):
                        lines.append(f"[{txt}]({href})")
                    else:
                        lines.append(txt)
        return "\n".join(lines)
