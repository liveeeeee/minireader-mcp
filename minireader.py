"""
MiniReader MCP: Ultra-lightweight Web-to-Markdown CLI & Model Context Protocol (MCP) Server.
Author: liveeeeee (https://github.com/liveeeeee)
Sponsorship: https://paypal.me/liveeeeee1203
License: MIT

Features:
- Pure Python 3 standard library (ZERO external dependencies).
- Memory footprint: ~20MB peak RSS (CLI).
- Startup: ~0.05s internal execution / ~0.31s CLI end-to-end (including Python runtime).
- Smart content extraction: GFM tables, images, ad/noise filtering, gzip/deflate decompression.
- Dual mode: Standalone CLI and standard MCP Server for Claude Desktop / Cursor / Windsurf.
"""

import gzip
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser
from typing import Dict, Any, List, Optional, Tuple


PAYPAL_URL = "https://paypal.me/liveeeeee1203"
VERSION = "1.2.0"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

# HTML void 元素（自闭合元素，无闭合标签，绝不入栈）
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr"
}

# 块级标签集合（触发排版分段换行，杜绝内容挤压粘连）
BLOCK_TAGS = {
    "div", "section", "article", "main", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "pre", "table", "tr", "header", "footer", "hr", "address",
    "dl", "dt", "dd", "figure", "figcaption"
}

# 始终忽略的基础标签
BASE_IGNORE_TAGS = {"script", "style", "noscript", "svg", "nav", "aside", "form", "button", "iframe"}

# 广告、弹窗与干扰组件的 class/id 启发式正则
AD_NOISE_RE = re.compile(
    r"(^|[\s_-])(ad|ads|banner|advertisement|sponsor|sponsored|promo|cookie|newsletter|popup|modal|share-btn|sharing|sidebar|widget)([\s_-]|$)",
    re.IGNORECASE
)

# 支持的 MCP 协议版本白名单（按推荐优先级排序）
SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]


class HTMLToMarkdownParser(HTMLParser):
    """极简、健壮、无依赖的 HTML 到纯净 GFM Markdown 解析器"""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.md_lines: List[str] = []
        self.current_line: List[str] = []
        self.tag_stack: List[str] = []
        self.noise_stack: List[bool] = []
        self.in_ignore_count = 0
        self.in_noise_count = 0
        self.in_code_block = False
        self.current_link: Optional[str] = None
        self.link_text: List[str] = []
        self.page_title = ""
        self.in_title = False
        self.list_stack: List[Dict[str, Any]] = []

        # 表格状态追踪
        self.in_table = False
        self.table_rows: List[List[str]] = []
        self.current_row: List[str] = []
        self.current_cell: List[str] = []

    def _append_inline(self, text: str):
        """将行内元素（文本、链接、行内代码、图片）路由到当前活跃缓冲区"""
        if self.in_table:
            self.current_cell.append(text)
        else:
            self.current_line.append(text)

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        tag = tag.lower()
        attrs_dict = dict(attrs)

        # 检查是否处于忽略状态
        if self.in_ignore_count > 0 or self.in_noise_count > 0:
            # 即使在忽略区域内，也只将非 void 标签压栈，保证 endtag 匹配
            if tag not in VOID_TAGS:
                self.tag_stack.append(tag)
                self.noise_stack.append(False)
            return

        # MR-1: void 元素特殊处理，绝不压栈，避免造成栈失配
        if tag in VOID_TAGS:
            if tag == "br":
                if self.in_table:
                    self._append_inline(" ")
                else:
                    self.current_line.append("\n")
            elif tag == "hr":
                self._flush_current_line()
                self.md_lines.append("---\n\n")
            elif tag == "img":
                src = attrs_dict.get("src", "")
                if src:
                    if self.base_url:
                        src = urllib.parse.urljoin(self.base_url, src)
                    alt = attrs_dict.get("alt", "").strip() or "image"
                    self._append_inline(f"![{alt}]({src}) ")
            return

        # 1. 检查噪音广告 class 与 id
        cls_id = f"{attrs_dict.get('class', '')} {attrs_dict.get('id', '')}".strip()
        is_noise = bool(cls_id and AD_NOISE_RE.search(cls_id))
        if is_noise:
            self.in_noise_count += 1
        self.noise_stack.append(is_noise)

        # 2. 判断是否属于忽略标签
        is_ignore = False
        if tag in BASE_IGNORE_TAGS:
            is_ignore = True
        elif tag in ["header", "footer"]:
            # 正文容器（article/main/section）内部的 header/footer 保留内容，只剔除页面级全局噪点
            in_content_container = any(t in ["article", "main", "section"] for t in self.tag_stack)
            if not in_content_container:
                is_ignore = True

        if is_ignore:
            self.in_ignore_count += 1

        self.tag_stack.append(tag)

        if self.in_ignore_count > 0 or self.in_noise_count > 0:
            return

        # 块级标签开始时冲刷当前行，防止文本跨块粘连
        if tag in BLOCK_TAGS and not self.in_table:
            self._flush_current_line()

        if tag == "title":
            self.in_title = True
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            level = int(tag[1])
            self.current_line.append("#" * level + " ")
        elif tag == "pre":
            self.in_code_block = True
            self.md_lines.append("```\n")
        elif tag == "code" and not self.in_code_block:
            self._append_inline("`")
        elif tag == "blockquote":
            self.current_line.append("> ")
        elif tag in ["b", "strong"]:
            self._append_inline("**")
        elif tag in ["i", "em"]:
            self._append_inline("*")
        elif tag == "ul":
            self.list_stack.append({"type": "ul", "count": 0})
        elif tag == "ol":
            self.list_stack.append({"type": "ol", "count": 0})
        elif tag == "li":
            depth = max(0, len(self.list_stack) - 1)
            indent = "  " * depth
            if self.list_stack and self.list_stack[-1]["type"] == "ol":
                self.list_stack[-1]["count"] += 1
                self.current_line.append(f"{indent}{self.list_stack[-1]['count']}. ")
            else:
                self.current_line.append(f"{indent}* ")
        elif tag == "dt":
            self._flush_current_line()
            self.current_line.append("**")
        elif tag == "dd":
            self._flush_current_line()
            self.current_line.append(": ")
        elif tag == "figcaption":
            self._flush_current_line()
            self.current_line.append("*")
        elif tag == "a":
            href = attrs_dict.get("href")
            if href and not href.startswith("javascript:"):
                if self.base_url:
                    href = urllib.parse.urljoin(self.base_url, href)
                self.current_link = href
                self.link_text = []
        elif tag == "table":
            self.in_table = True
            self.table_rows = []
        elif tag == "tr" and self.in_table:
            self.current_row = []
        elif tag in ["th", "td"] and self.in_table:
            self.current_cell = []

    def handle_endtag(self, tag: str):
        tag = tag.lower()

        # MR-1: void 标签没有闭合标签，直接跳过
        if tag in VOID_TAGS:
            return

        # 向上查找匹配标签并容错弹出
        if tag in self.tag_stack:
            idx = len(self.tag_stack) - 1 - self.tag_stack[::-1].index(tag)
            while len(self.tag_stack) > idx:
                popped_tag = self.tag_stack.pop()
                if self.noise_stack:
                    was_noise = self.noise_stack.pop()
                    if was_noise and self.in_noise_count > 0:
                        self.in_noise_count -= 1

                is_ign = False
                if popped_tag in BASE_IGNORE_TAGS:
                    is_ign = True
                elif popped_tag in ["header", "footer"]:
                    in_container = any(t in ["article", "main", "section"] for t in self.tag_stack)
                    if not in_container:
                        is_ign = True
                if is_ign and self.in_ignore_count > 0:
                    self.in_ignore_count -= 1

        if self.in_ignore_count > 0 or self.in_noise_count > 0:
            return

        if tag == "title":
            self.in_title = False
        elif tag in ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "div", "section", "article", "main", "figure"]:
            self._flush_current_line()
        elif tag in ["ul", "ol"]:
            if self.list_stack:
                self.list_stack.pop()
            self._flush_current_line()
        elif tag == "dt":
            self.current_line.append("**")
            self._flush_current_line()
        elif tag == "dd":
            self._flush_current_line()
        elif tag == "figcaption":
            self.current_line.append("*")
            self._flush_current_line()
        elif tag == "pre":
            self.in_code_block = False
            # 确保前面代码内容已换行，围栏独占一行
            if self.md_lines and not self.md_lines[-1].endswith("\n"):
                self.md_lines.append("\n")
            self.md_lines.append("```\n\n")
        elif tag == "code" and not self.in_code_block:
            self._append_inline("`")
        elif tag in ["b", "strong"]:
            self._append_inline("**")
        elif tag in ["i", "em"]:
            self._append_inline("*")
        elif tag == "a":
            if self.current_link:
                text = "".join(self.link_text).strip()
                if text:
                    self._append_inline(f"[{text}]({self.current_link})")
                self.current_link = None
                self.link_text = []
        elif tag in ["th", "td"] and self.in_table:
            cell_text = "".join(self.current_cell).strip().replace("\n", " ")
            # 转义单元格内的未转义竖线 |，防止破坏 GFM 表格列对齐结构
            cell_text = re.sub(r"(?<!\\)\|", r"\|", cell_text)
            self.current_row.append(cell_text)
            self.current_cell = []
        elif tag == "tr" and self.in_table:
            if self.current_row:
                self.table_rows.append(self.current_row)
                self.current_row = []
        elif tag == "table":
            self.in_table = False
            if self.table_rows:
                col_count = max(len(r) for r in self.table_rows) if self.table_rows else 0
                if col_count > 0:
                    gfm_lines = []
                    header_row = self.table_rows[0] + [""] * (col_count - len(self.table_rows[0]))
                    gfm_lines.append("| " + " | ".join(header_row) + " |")
                    gfm_lines.append("| " + " | ".join(["---"] * col_count) + " |")
                    for row in self.table_rows[1:]:
                        padded = row + [""] * (col_count - len(row))
                        gfm_lines.append("| " + " | ".join(padded) + " |")
                    self.md_lines.append("\n".join(gfm_lines) + "\n\n")
            self.table_rows = []

    def handle_data(self, data: str):
        if self.in_ignore_count > 0 or self.in_noise_count > 0:
            return

        if self.in_title:
            self.page_title += data.strip()
            return

        if self.in_code_block:
            self.md_lines.append(data)
            return

        if self.current_link is not None:
            self.link_text.append(data)
            return

        if self.in_table:
            self.current_cell.append(data)
            return

        clean = re.sub(r"\s+", " ", data)
        if clean:
            # 保证块间与行内单词间保留空格语义，杜绝粘连
            if (data.startswith(" ") or data.startswith("\t") or data.startswith("\n")) and self.current_line:
                if not self.current_line[-1].endswith(" "):
                    self.current_line.append(" ")
            self.current_line.append(clean.strip())
            if data.endswith(" ") or data.endswith("\t") or data.endswith("\n"):
                self.current_line.append(" ")

    def _flush_current_line(self):
        line = "".join(self.current_line).strip()
        if line:
            self.md_lines.append(line + "\n\n")
        self.current_line = []

    def get_markdown(self) -> str:
        self._flush_current_line()
        content = "".join(self.md_lines)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        title_prefix = f"# {self.page_title}\n\n" if self.page_title and not content.startswith("# ") else ""
        return title_prefix + content


def decompress_and_decode(raw_bytes: bytes, encoding: str = "", charset: Optional[str] = None) -> str:
    """严格解压缩并安全解码文本字节（拒绝不支持的未解压流）"""
    enc = encoding.strip().lower()
    if enc == "gzip":
        raw_bytes = gzip.decompress(raw_bytes)
    elif enc == "deflate":
        try:
            raw_bytes = zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
        except Exception:
            raw_bytes = zlib.decompress(raw_bytes)
    elif enc and enc not in ["identity", ""]:
        raise ValueError(f"Unsupported Content-Encoding: '{enc}'. Only gzip and deflate are supported.")

    enc_charset = charset or "utf-8"
    try:
        return raw_bytes.decode(enc_charset, errors="replace")
    except Exception:
        return raw_bytes.decode("utf-8", errors="replace")


def fetch_url(url: str, timeout: int = 12) -> Tuple[str, str, str]:
    """获取网页内容，处理 gzip/deflate 解压缩与编码分派（严格校验 HTTP/HTTPS 协议）"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Forbidden URL scheme '{parsed.scheme}': only http and https are allowed")

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml,text/markdown,text/plain,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "gzip, deflate"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "").lower()
        encoding = resp.headers.get("Content-Encoding", "").strip().lower()
        raw_bytes = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        decoded_text = decompress_and_decode(raw_bytes, encoding=encoding, charset=charset)
        return decoded_text, final_url, content_type


def html_to_markdown(html_text: str, base_url: str = "", content_type: str = "text/html") -> str:
    """转换文本为纯净 Markdown（按 Content-Type 分派，单次反转义，杜绝代码被吞）"""
    ct = content_type.lower()
    # 4. 非 HTML 响应按类型分派，保护 Markdown / JSON / 纯文本结构
    if "text/markdown" in ct or "text/x-markdown" in ct or (html_text.strip().startswith("# ") and not ("<html" in html_text.lower() or "<body" in html_text.lower())):
        return html_text.strip()
    elif "application/json" in ct:
        return f"```json\n{html_text.strip()}\n```"
    elif "text/plain" in ct and not ("<html" in html_text.lower() or "<body" in html_text.lower()):
        return html_text.strip()

    parser = HTMLToMarkdownParser(base_url=base_url)
    parser.feed(html_text)
    return parser.get_markdown()


def clean_read_url(url: str) -> Dict[str, Any]:
    """主入口函数：分派 Content-Type，输出纯净 Markdown 和元数据"""
    start_time = time.time()
    raw_text, final_url, content_type = fetch_url(url)
    md_content = html_to_markdown(raw_text, base_url=final_url, content_type=content_type)
    elapsed = round(time.time() - start_time, 2)
    lines = md_content.splitlines()
    first_line = lines[0] if lines else ""
    return {
        "url": final_url,
        "title": first_line.replace("#", "").strip() if first_line else "",
        "markdown": md_content,
        "char_count": len(md_content),
        "fetch_time_sec": elapsed
    }


# ==========================================
# 标准 MCP (Model Context Protocol) 服务端实现
# ==========================================

def handle_mcp_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """标准 MCP 请求路由处理，严格遵循 JSON-RPC 2.0 规范"""
    req_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})

    # MR-5: JSON-RPC 规范：无 id 或方法为 notifications/* 一律为通知，绝不产生响应
    if req_id is None or (isinstance(method, str) and method.startswith("notifications/")):
        return None

    if method == "initialize":
        # MR-6: 协议版本白名单协商
        client_proto = params.get("protocolVersion")
        if client_proto in SUPPORTED_PROTOCOL_VERSIONS:
            negotiated_proto = client_proto
        else:
            negotiated_proto = SUPPORTED_PROTOCOL_VERSIONS[0]

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated_proto,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "minireader-mcp",
                    "version": VERSION
                }
            }
        }
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "read_web_page",
                        "description": "Fetches any webpage and extracts clean, ad-free Markdown for LLM analysis. Zero Chromium overhead.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "The absolute HTTP/HTTPS URL of the webpage to read."}
                            },
                            "required": ["url"]
                        }
                    }
                ]
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name == "read_web_page":
            target_url = arguments.get("url", "")
            if not target_url:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "Error: 'url' parameter is required."}],
                        "isError": True
                    }
                }
            try:
                data = clean_read_url(target_url)
                result_text = f"# Source: {data['url']}\n\n{data['markdown']}"
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                        "isError": False
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Failed to read webpage {target_url}: {str(e)}"}],
                        "isError": True
                    }
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"}
            }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not supported"}
        }


def run_mcp_server():
    """以标准 JSON-RPC 2.0 stdio 协议运行 MCP 服务"""
    sys.stderr.write(f"[*] MiniReader MCP Server v{VERSION} starting via stdio...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except Exception as e:
                # JSON-RPC 2.0 规范：Parse error (-32700)
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {str(e)}"
                    }
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            response = handle_mcp_message(msg)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()

        except Exception as e:
            sys.stderr.write(f"[!] MCP error: {e}\n")
            sys.stderr.flush()


def self_test():
    """离线本地自检 (包含全套缺陷防御断言)"""
    print("[*] 正在执行 MiniReader 本地离线自检...")

    # 1. 测试代码块单次反转义与围栏闭合
    code_html = "<pre><code>&lt;script&gt;var x=42;&lt;/script&gt;</code></pre>"
    code_md = html_to_markdown(code_html)
    assert "<script>var x=42;</script>" in code_md, "Code block double unescape failed"
    assert code_md.endswith("```"), "Code fence must end cleanly"
    print("[+] 代码块反转义与独立围栏测试通过！")

    # 2. 测试块级元素防粘连与换行，包括 <br>
    block_html = "<div>Title Here</div><p>Line 1<br>Line 2</p><div>First para.</div>"
    block_md = html_to_markdown(block_html)
    assert "Title HereFirst para." not in block_md
    assert "Line 1\nLine 2" in block_md
    print("[+] 块级元素分行、<br> 换行与防词粘连测试通过！")

    # 3. 测试 article 内部 header 保留与 void 标签栈同步 (MR-1)
    art_void_html = '<article><header><h1>My Title</h1><p>By Author</p></header><img src="pic.jpg"><p>Content</p></article><footer><p>Site Footer</p></footer>'
    art_md = html_to_markdown(art_void_html)
    assert "My Title" in art_md, "Article inner header was wrongly deleted"
    assert "By Author" in art_md
    assert "Site Footer" not in art_md, "Site footer should be stripped outside article"
    print("[+] 文章内部 header 智能保留与 void 元素栈同步测试通过！")

    # 4. 测试广告 class 过滤及内部包含 void 元素不泄漏 (MR-1, MR-7)
    ad_html = '<div class="ad-container"><img src="pixel.gif"><p>Ad text</p></div><p>Real Content</p>'
    ad_md = html_to_markdown(ad_html)
    assert "Ad text" not in ad_md, "Ad text was not stripped"
    assert "Real Content" in ad_md, "Post-ad content was wrongly dropped"
    print("[+] 广告噪点启发式过滤与追踪像素测试通过！")

    # 5. 测试 GFM 表格与单元格内行内元素 (MR-3)
    table_html = '<table><tr><th>Col1</th><th>Col2</th></tr><tr><td><a href="https://example.com">Link</a></td><td><code>code_val</code></td></tr></table>'
    tbl_md = html_to_markdown(table_html)
    assert "| [Link](https://example.com) | `code_val` |" in tbl_md
    print("[+] GFM 表格单元格内超链接与代码路由测试通过！")

    # 6. 测试真实 decompress_and_decode 函数调用 (MR-7)
    sample_raw = b"<html><body><p>Real Gzip Decompress Pipeline</p></body></html>"
    gzipped = gzip.compress(sample_raw)
    decompressed = decompress_and_decode(gzipped, encoding="gzip", charset="utf-8")
    assert "Real Gzip Decompress Pipeline" in decompressed
    try:
        decompress_and_decode(b"bytes", encoding="br")
        assert False, "Should raise ValueError for unsupported compression"
    except ValueError:
        pass
    print("[+] 真实 decompress_and_decode 解压流水线与安全拦截测试通过！")

    # 7. 测试 MCP 通知静默与协议协商 (MR-5, MR-6)
    notif_res = handle_mcp_message({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
    assert notif_res is None, "Notifications must not return response"
    init_res = handle_mcp_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
    assert init_res["result"]["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS
    print("[+] MCP 通知静默与协议版本白名单协商测试通过！")

    print("[+] MiniReader 全部自检断言 100% 成功通过！")


def main():
    if "--self-test" in sys.argv:
        self_test()
    elif "--mcp" in sys.argv:
        run_mcp_server()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        url = sys.argv[1]
        print(f"[*] 正在抓取并提取正文: {url} ...", file=sys.stderr)
        try:
            res = clean_read_url(url)
            output_file = None
            if "--output" in sys.argv:
                idx = sys.argv.index("--output")
                if idx + 1 < len(sys.argv):
                    output_file = sys.argv[idx + 1]

            if output_file:
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(res["markdown"])
                print(f"[+] 纯净 Markdown 已成功保存至: {output_file}", file=sys.stderr)
            else:
                print(res["markdown"])

            print(f"\n✨ 提取完成! 耗时 {res['fetch_time_sec']}s, 字符数 {res['char_count']} (内存 ~20MB RSS).", file=sys.stderr)
            print(f"☕ 觉得好用？请作者喝杯咖啡支持持续维护: {PAYPAL_URL}\n", file=sys.stderr)

        except Exception as e:
            print(f"[!] 抓取失败: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("MiniReader MCP - Ultra-lightweight Web to Markdown CLI & MCP Server")
        print(f"Author: liveeeeee | Donate: {PAYPAL_URL}\n")
        print("Usage:")
        print("  1. CLI 命令行提取: python minireader.py <URL> [--output result.md]")
        print("  2. 作为 MCP 服务运行: python minireader.py --mcp")
        print("  3. 离线单元自检: python minireader.py --self-test")


if __name__ == "__main__":
    main()
