# ⚡ MiniReader MCP

> **Ultra-lightweight Web-to-Markdown CLI & Model Context Protocol (MCP) Server.**  
> Zero Chromium bloat, zero external dependencies, ~20MB peak RSS memory footprint.  
> 纯原生标准库构建、零外部依赖、极致轻量的网页正文提取与 Markdown 转换工具。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/liveeeeee1203)
[![GitHub Sponsor](https://img.shields.io/badge/Sponsor-GitHub-ea4aaa?style=for-the-badge&logo=github)](https://github.com/sponsors/liveeeeee)

---

## 💡 Why MiniReader? (为什么做这个工具？)

Existing web-reading solutions for AI agents (Playwright MCP, Puppeteer, Selenium) are **massive bloatware**:
- ❌ Require downloading 500MB+ Chromium binaries.
- ❌ Consume 800MB ~ 1.5GB of RAM just to read a single blog post.
- ❌ Frequently crash on low-spec VPS or headless Linux containers.
- ❌ External cloud APIs (Jina / Firecrawl) charge high fees or hit 429 rate limits.

**MiniReader solves this completely:**
- ✅ **Pure Python Standard Library**: ZERO `pip install` required.
- ✅ **Tiny Footprint**: ~0.05s internal execution / ~0.31s CLI end-to-end, ~20MB peak RSS RAM.
- ✅ **Smart Readability**: Strips ads, navigation, banners, cookies, and tracking scripts via heuristic class/id detection and tag filtering.
- ✅ **Full Format Support**: Transparent gzip/deflate decompression, GFM table conversion, and image URL resolution.
- ✅ **Dual-Mode**: Works as an instant terminal CLI and a standard **MCP Server** for **Claude Desktop / Cursor / Windsurf**.

---

## 🚀 Quick Start (快速开始)

### 1. Terminal CLI (命令行一键提取)

```bash
# Print clean Markdown to terminal
python minireader.py https://example.com/blog/article-1

# Save directly to a markdown file
python minireader.py https://example.com/blog/article-1 --output article.md
```

### 2. Use with Claude Desktop / Cursor (作为 MCP Server)

Add MiniReader to your `claude_desktop_config.json` or Cursor MCP settings:

```json
{
  "mcpServers": {
    "minireader": {
      "command": "python",
      "args": ["c:/Users/26794/Desktop/AI自动化/minireader-mcp/minireader.py", "--mcp"]
    }
  }
}
```

Now your AI agent has a native, lightweight `read_web_page` tool without installing Chrome!

---

## 🧪 Self-Test (离线自检)

```bash
python minireader.py --self-test
```

---

## ☕ Support & Donations (赞助与打赏)

MiniReader is free and open-source. If this tool saved you server costs, time, or headaches:

👉 **[Buy Me a Coffee via PayPal (支持作者)](https://paypal.me/liveeeeee1203)**  
*Every donation directly supports maintenance, new features, and coffee-fueled coding!*

---

## 📄 License

MIT License © 2026 [liveeeeee](https://github.com/liveeeeee)
