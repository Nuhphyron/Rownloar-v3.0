# ROWNLOAR PRO v3.0 – Speed Limit Bypasser 🚀

**Rownloar Pro** is an advanced multi‑threaded download accelerator designed to **bypass per‑connection speed limits** imposed by many file hosting services. By splitting files into chunks and downloading them simultaneously with multiple connections, it can saturate your available bandwidth and dramatically increase download speeds.

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Nuhphyron/Scr4py-Proxy-Scraper&type=date&legend=top-left)](https://www.star-history.com/#Nuhphyron/Scr4py-Proxy-Scraper&type=date&legend=top-left)

---

## ✨ Features

- **Multi‑threaded chunk downloading** – Split any file into configurable chunks (default 10 MB) and download them in parallel. Bypasses server‑side limits that restrict speed per TCP connection.
- **Persistent resume** – Saves download state to a JSON file (`.rownloar.json`). If the download is interrupted, simply run the script again and it will resume exactly where it left off.
- **Dynamic chunk queue** – Threads pull chunks from a queue, ensuring perfect load balancing even if some threads finish early.
- **Proxy rotation** – Provide a list of proxies (HTTP/HTTPS) and the engine will rotate through them for each request. Supports proxy authentication.
- **Multi‑URL batch download** – Input a single URL, a list interactively, or a text file containing many URLs. All will be processed sequentially.
- **Smart URL resolution** – Follows redirects, detects HTML pages, and attempts to extract the real download link from common patterns (`href` attributes containing `.zip`, `.rar`, `data-url`, etc.).
- **Retry with exponential backoff** – Each failed chunk is retried up to 5 times with increasing delays, improving reliability on flaky connections.
- **Checksum verification** – Verify downloaded files using MD5, SHA256, or any hash supported by `hashlib` (e.g., `md5:hash` or `sha256:hash`).
- **HTTP/2 support** – If `httpx` is installed, the engine can use HTTP/2 for faster, multiplexed downloads (falls back gracefully to `requests`).
- **Rich interactive console** – Beautiful progress bars, per‑thread statistics, summary tables, and colour‑coded prompts powered by the `rich` library.
- **User‑Agent rotation** – Rotates between a set of modern browser User‑Agents to avoid fingerprinting.
- **Custom headers** – Add any extra HTTP headers (e.g., `Referer`, `Authorization`).
- **Output directory selection** – Choose where to save downloaded files; directories are created automatically.
- **Clear screen on restart** – After each download session, the console is cleared for a fresh start.

---

## 📦 Requirements

- **Python 3.13** (or 3.8+)
- **Required packages**:
  - `requests`
  - `rich`
- **Optional** (for HTTP/2 support):
  - `httpx`

Install the required packages with:

```bash
pip install requests rich
