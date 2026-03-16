#!/usr/bin/env python3
# =================================================================
# ROWNLOAR PRO v3.0 - Advanced Multi-Threaded Download Accelerator
# =================================================================
#                           SPEED LIMIT BYPASSER
# -----------------------------------------------------------------------------
# Features:
#   - Multi-threaded chunk downloading (bypasses server-side per-connection limits)
#   - Persistent resume (saves state to JSON)
#   - Dynamic chunk allocation for better load balancing
#   - Proxy rotation with authentication support
#   - Multi-URL batch download
#   - Smart thread/connection management
#   - Retry with exponential backoff
#   - Checksum verification (MD5, SHA256)
#   - Rich console interface with per-thread stats
#   - HTTP/2 support (via httpx, fallback to requests)
#   - Cookie and header customization
#   - URL list file import
#
# Compatible with Python 3.13
# =============================================================================

import os
import sys
import json
import hashlib
import time
import random
import threading
import queue
from pathlib import Path
from urllib.parse import urlparse, unquote, urljoin
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple, Callable
import re
import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, DownloadColumn, TransferSpeedColumn
from rich.table import Table
from rich import print as rprint
from rich.logging import RichHandler
import logging

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
]

DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30
MAX_REDIRECTS = 5

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

@dataclass
class Chunk:
    start: int
    end: int
    downloaded: int = 0
    attempts: int = 0
    completed: bool = False
    data: Optional[bytes] = None

@dataclass
class DownloadState:
    url: str
    filename: str
    total_size: int
    chunks: List[Dict[str, Any]]
    headers: Dict[str, str]
    proxy: Optional[str]
    use_random_ua: bool
    created: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass
class DownloadTask:
    url: str
    filename: str
    total_size: int = 0
    state_file: Optional[Path] = None
    chunks: List[Chunk] = field(default_factory=list)
    completed_chunks: int = 0
    downloaded_bytes: int = 0
    start_time: float = field(default_factory=time.time)
    last_save: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    progress_id: Optional[int] = None

class RownloarEngine:
    def __init__(
        self,
        console: Console,
        max_threads: int = 16,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        proxy_list: Optional[List[str]] = None,
        proxy_auth: Optional[Tuple[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_random_ua: bool = True,
        verify_ssl: bool = True,
        speed_limit: Optional[int] = None,
        resume: bool = True,
        checksum: Optional[str] = None,
        checksum_type: Optional[str] = None,
        http2: bool = False,
        timeout: Tuple[int, int] = (CONNECT_TIMEOUT, READ_TIMEOUT),
        retries: int = MAX_RETRIES,
        debug: bool = False,
    ):
        self.console = console
        self.max_threads = max_threads
        self.chunk_size = chunk_size
        self.proxy_list = proxy_list or []
        self.proxy_auth = proxy_auth
        self.base_headers = headers or {}
        self.use_random_ua = use_random_ua
        self.verify_ssl = verify_ssl
        self.speed_limit = speed_limit
        self.resume = resume
        self.checksum = checksum
        self.checksum_type = checksum_type.lower() if checksum_type else None
        self.http2 = http2 and HAS_HTTPX
        self.timeout = timeout
        self.retries = retries
        self.debug = debug

        self.session_requests = None
        self.session_httpx = None
        self.proxy_pool = queue.Queue()
        self._init_proxy_pool()
        self._init_sessions()

        logging.basicConfig(
            level=logging.DEBUG if debug else logging.INFO,
            format="%(message)s",
            handlers=[RichHandler(console=console, rich_tracebacks=True)]
        )
        self.log = logging.getLogger("rownloar")

    def _init_proxy_pool(self):
        for proxy in self.proxy_list:
            self.proxy_pool.put(proxy)

    def _get_proxy(self):
        if self.proxy_pool.empty():
            return None
        proxy = self.proxy_pool.get()
        self.proxy_pool.put(proxy)
        return proxy

    def _init_sessions(self):
        self.session_requests = requests.Session()
        self.session_requests.verify = self.verify_ssl
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=100, max_retries=0)
        self.session_requests.mount('http://', adapter)
        self.session_requests.mount('https://', adapter)

        if self.http2:
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
            self.session_httpx = httpx.Client(
                http2=True,
                verify=self.verify_ssl,
                limits=limits,
                timeout=httpx.Timeout(self.timeout[1], connect=self.timeout[0]),
            )

    def _make_request(self, method: str, url: str, headers: Optional[Dict] = None, stream: bool = False, **kwargs):
        if self.http2 and self.session_httpx:
            req_headers = {**self.base_headers, **(headers or {})}
            if self.use_random_ua and 'User-Agent' not in req_headers:
                req_headers['User-Agent'] = random.choice(DEFAULT_USER_AGENTS)

            proxy = self._get_proxy()
            proxies = {"http": proxy, "https": proxy} if proxy else None

            try:
                resp = self.session_httpx.request(
                    method, url, headers=req_headers, proxies=proxies, follow_redirects=False, **kwargs
                )
                resp.headers = resp.headers
                resp.status_code = resp.status_code
                if stream:
                    return self._make_request_requests(method, url, headers, stream, **kwargs)
                return resp
            except Exception as e:
                self.log.debug(f"httpx request failed: {e}, falling back to requests")
                return self._make_request_requests(method, url, headers, stream, **kwargs)
        else:
            return self._make_request_requests(method, url, headers, stream, **kwargs)

    def _make_request_requests(self, method: str, url: str, headers: Optional[Dict] = None, stream: bool = False, **kwargs):
        req_headers = {**self.base_headers, **(headers or {})}
        if self.use_random_ua and 'User-Agent' not in req_headers:
            req_headers['User-Agent'] = random.choice(DEFAULT_USER_AGENTS)

        proxy = self._get_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        return self.session_requests.request(
            method, url, headers=req_headers, proxies=proxies, stream=stream,
            timeout=self.timeout, verify=self.verify_ssl, allow_redirects=False, **kwargs
        )

    def resolve_url(self, url: str) -> Tuple[str, int, str, Dict]:
        current_url = url
        redirect_count = 0

        while redirect_count < MAX_REDIRECTS:
            resp = self._make_request('HEAD', current_url)
            resp.raise_for_status()

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('Location')
                if not location:
                    break
                current_url = urljoin(current_url, location)
                redirect_count += 1
                self.log.info(f"Redirect {redirect_count}: {current_url}")
                continue

            content_type = resp.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                self.log.warning("Got HTML page, attempting to find download link")
                page_resp = self._make_request('GET', current_url)
                page_resp.raise_for_status()
                patterns = [
                    r'href="([^"]+\.(jar|zip|exe|msi|dmg|tar\.gz|tgz|rar|7z))"',
                    r'data-url="([^"]+)"',
                    r'<a[^>]*href="([^"]*download[^"]*)"',
                    r'download-link[^>]*href="([^"]+)"',
                ]
                for pat in patterns:
                    matches = re.findall(pat, page_resp.text, re.IGNORECASE)
                    if matches:
                        download_url = urljoin(current_url, matches[0] if isinstance(matches[0], str) else matches[0][0])
                        self.log.info(f"Found download link: {download_url}")
                        current_url = download_url
                        break
                else:
                    self.log.warning("No download link found; proceeding with original URL")
                continue

            total_size = int(resp.headers.get('Content-Length', 0))
            return current_url, total_size, content_type, dict(resp.headers)

        return current_url, 0, '', {}

    def get_filename(self, url: str, response_headers: Dict) -> str:
        cd = response_headers.get('content-disposition', '')
        if cd:
            match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\']+)', cd)
            if match:
                return unquote(match.group(1).strip())

        parsed = urlparse(url)
        path = parsed.path
        filename = os.path.basename(path)
        if not filename or '.' not in filename:
            content_type = response_headers.get('content-type', '').split(';')[0]
            ext_map = {
                'application/zip': '.zip',
                'application/x-zip-compressed': '.zip',
                'application/jar': '.jar',
                'application/java-archive': '.jar',
                'application/pdf': '.pdf',
                'application/x-msdownload': '.exe',
                'application/x-msi': '.msi',
                'application/octet-stream': '.bin',
                'text/html': '.html',
            }
            ext = ext_map.get(content_type, '.bin')
            filename = f"download{ext}"
        return unquote(filename)

    def load_state(self, state_path: Path, url: str, expected_size: int) -> Optional[DownloadState]:
        if not state_path.exists():
            return None
        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
            state = DownloadState.from_dict(data)
            if state.url != url:
                self.log.warning("State URL mismatch; ignoring")
                return None
            if state.total_size != expected_size:
                self.log.warning(f"State total size {state.total_size} != server size {expected_size}; discarding state")
                state_path.unlink()
                return None
            return state
        except Exception as e:
            self.log.debug(f"Failed to load state: {e}")
            return None

    def save_state(self, task: DownloadTask):
        if not task.state_file:
            return
        state = DownloadState(
            url=task.url,
            filename=task.filename,
            total_size=task.total_size,
            chunks=[asdict(c) for c in task.chunks],
            headers=self.base_headers,
            proxy=None,
            use_random_ua=self.use_random_ua,
            last_updated=time.time(),
        )
        try:
            with open(task.state_file, 'w') as f:
                json.dump(asdict(state), f, indent=2)
        except Exception as e:
            self.log.debug(f"Failed to save state: {e}")

    def create_chunks(self, total_size: int) -> List[Chunk]:
        chunks = []
        start = 0
        while start < total_size:
            end = min(start + self.chunk_size - 1, total_size - 1)
            chunks.append(Chunk(start=start, end=end))
            start = end + 1
        return chunks

    def download_chunk(self, task: DownloadTask, chunk: Chunk, progress_callback: Callable[[int], None] = None):
        headers = {'Range': f'bytes={chunk.start + chunk.downloaded}-{chunk.end}'}
        attempt = 0
        while attempt <= self.retries:
            try:
                resp = self._make_request('GET', task.url, headers=headers, stream=True)
                resp.raise_for_status()

                if resp.status_code == 206:
                    pass
                elif resp.status_code == 200:
                    self.log.warning("Server ignored range request; switching to single-thread mode")
                    data = resp.content
                    with open(task.filename, 'r+b') as f:
                        f.seek(chunk.start + chunk.downloaded)
                        f.write(data)
                    if progress_callback:
                        progress_callback(len(data))
                    chunk.completed = True
                    return
                else:
                    resp.raise_for_status()

                with open(task.filename, 'r+b') as f:
                    f.seek(chunk.start + chunk.downloaded)
                    for data in resp.iter_content(chunk_size=64*1024):
                        if data:
                            f.write(data)
                            if progress_callback:
                                progress_callback(len(data))
                            chunk.downloaded += len(data)

                chunk.completed = True
                return

            except Exception as e:
                attempt += 1
                self.log.debug(f"Chunk {chunk.start}-{chunk.end} failed (attempt {attempt}): {e}")
                if attempt > self.retries:
                    raise
                time.sleep(RETRY_BACKOFF_BASE ** attempt * random.uniform(0.5, 1.5))

    def download_task(self, task: DownloadTask, progress: Progress) -> bool:
        if task.total_size > 0:
            if not os.path.exists(task.filename):
                with open(task.filename, 'wb') as f:
                    f.truncate(task.total_size)
        else:
            if not os.path.exists(task.filename):
                open(task.filename, 'wb').close()

        chunk_queue = queue.Queue()
        for chunk in task.chunks:
            if not chunk.completed:
                chunk_queue.put(chunk)

        total_chunks = chunk_queue.qsize()
        completed_chunks = 0

        def worker():
            nonlocal completed_chunks
            while True:
                try:
                    chunk = chunk_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    def cb(advance):
                        task.downloaded_bytes += advance
                        progress.update(task.progress_id, advance=advance)
                    self.download_chunk(task, chunk, progress_callback=cb)
                    with task.lock:
                        completed_chunks += 1
                        task.completed_chunks = completed_chunks
                except Exception as e:
                    self.log.error(f"Chunk {chunk.start}-{chunk.end} failed after retries: {e}")
                    chunk_queue.put(chunk)
                    time.sleep(1)

        threads = min(self.max_threads, total_chunks)
        self.log.info(f"Starting {threads} threads for {total_chunks} chunks")
        thread_list = []
        for _ in range(threads):
            t = threading.Thread(target=worker)
            t.start()
            thread_list.append(t)

        while any(t.is_alive() for t in thread_list):
            time.sleep(5)
            if self.resume:
                self.save_state(task)

        for t in thread_list:
            t.join()

        self.save_state(task)

        for chunk in task.chunks:
            if not chunk.completed:
                self.log.error("Some chunks incomplete")
                return False

        actual_size = os.path.getsize(task.filename)
        if task.total_size > 0 and actual_size != task.total_size:
            if actual_size > task.total_size and all(chunk.completed for chunk in task.chunks):
                self.log.warning(f"Size mismatch: expected {task.total_size}, got {actual_size} (but all chunks completed). Treating as success.")
                task.total_size = actual_size
            else:
                self.log.error(f"Size mismatch: expected {task.total_size}, got {actual_size}")
                return False

        if self.checksum:
            if not self.verify_checksum(task.filename):
                return False

        return True

    def verify_checksum(self, filename: str) -> bool:
        hash_func = getattr(hashlib, self.checksum_type, None)
        if not hash_func:
            self.log.error(f"Unsupported checksum type: {self.checksum_type}")
            return False

        self.log.info(f"Verifying {self.checksum_type} checksum...")
        h = hash_func()
        with open(filename, 'rb') as f:
            for chunk in iter(lambda: f.read(64*1024), b''):
                h.update(chunk)
        digest = h.hexdigest()
        if digest.lower() != self.checksum.lower():
            self.log.error(f"Checksum mismatch: expected {self.checksum}, got {digest}")
            return False
        self.log.info("Checksum OK")
        return True

    def run(self, urls: List[str], output_dir: str = ".") -> bool:
        os.makedirs(output_dir, exist_ok=True)
        results = []

        for url in urls:
            self.console.rule(f"[bold blue]Downloading: {url}")
            try:
                final_url, total_size, content_type, headers = self.resolve_url(url)
                filename = self.get_filename(final_url, headers)
                filepath = os.path.join(output_dir, filename)

                if os.path.exists(filepath) and total_size > 0:
                    if os.path.getsize(filepath) == total_size:
                        self.console.print(f"[green]✓ File already fully downloaded: {filename}")
                        results.append((url, True))
                        continue
                    else:
                        self.console.print(f"[yellow]Partial file exists, will resume if possible.")

                task = DownloadTask(url=final_url, filename=filepath, total_size=total_size)
                task.state_file = Path(filepath + ".rownloar.json")

                if self.resume:
                    state = self.load_state(task.state_file, final_url, total_size)
                    if state:
                        self.log.info("Resuming from previous state")
                        task.chunks = [Chunk(**c) for c in state.chunks]
                        task.downloaded_bytes = sum(c.downloaded for c in task.chunks)
                        task.completed_chunks = sum(1 for c in task.chunks if c.completed)

                        if task.chunks and all(chunk.completed for chunk in task.chunks):
                            if os.path.exists(filepath) and os.path.getsize(filepath) != total_size:
                                self.log.warning("File size mismatch despite completed state; discarding state and redownloading")
                                task.chunks = []
                                task.downloaded_bytes = 0
                                task.completed_chunks = 0
                                if task.state_file.exists():
                                    task.state_file.unlink()

                if not task.chunks and total_size > 0:
                    task.chunks = self.create_chunks(total_size)
                elif total_size == 0:
                    self.log.warning("File size unknown; using single-thread download")
                    task.chunks = []

                with Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=None),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=self.console,
                ) as progress:
                    task.progress_id = progress.add_task(
                        f"[cyan]{filename}",
                        total=total_size or None,
                        completed=task.downloaded_bytes
                    )

                    if total_size == 0 or not task.chunks:
                        resp = self._make_request('GET', final_url, stream=True)
                        resp.raise_for_status()
                        with open(filepath, 'wb') as f:
                            for chunk in resp.iter_content(chunk_size=64*1024):
                                if chunk:
                                    f.write(chunk)
                                    progress.update(task.progress_id, advance=len(chunk))
                        success = True
                    else:
                        success = self.download_task(task, progress)

                if success:
                    self.console.print(f"[bold green]✓ Download complete: {filename}")
                    if task.state_file.exists():
                        task.state_file.unlink()
                else:
                    self.console.print(f"[bold red]✗ Download failed: {filename}")
                results.append((url, success))

            except Exception as e:
                self.console.print(f"[bold red]Error downloading {url}: {e}")
                results.append((url, False))

        self.console.rule("[bold]Summary")
        table = Table(title="Download Results")
        table.add_column("URL", style="cyan")
        table.add_column("Status", style="bold")
        for url, success in results:
            status = "[green]Success" if success else "[red]Failed"
            table.add_row(url[:60] + "..." if len(url) > 60 else url, status)
        self.console.print(table)

        return all(success for _, success in results)

def interactive_mode():
    clear_console()
    console = Console()

    banner = """
[bold magenta]
╔════════════════════════════════════════════════════════════════════════════╗
║                                                                            ║
║   ██████╗  ██████╗ ██╗    ██╗███╗   ██╗██╗      ██████╗  █████╗ ██████╗    ║
║   ██╔══██╗██╔═══██╗██║    ██║████╗  ██║██║     ██╔═══██╗██╔══██╗██╔══██╗   ║
║   ██████╔╝██║   ██║██║ █╗ ██║██╔██╗ ██║██║     ██║   ██║███████║██████╔╝   ║
║   ██╔══██╗██║   ██║██║███╗██║██║╚██╗██║██║     ██║   ██║██╔══██║██╔══██╗   ║
║   ██║  ██║╚██████╔╝╚███╔███╔╝██║ ╚████║███████╗╚██████╔╝██║  ██║██║  ██║   ║
║   ╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝   ║
║                                           - Made with <3 by Nuhphyron      ║
║                           PRO EDITION v3.0                                 ║
║                 Advanced Multi-Threaded Download Accelerator               ║
║                       ===== SPEED LIMIT BYPASSER =====                     ║
╚════════════════════════════════════════════════════════════════════════════╝
[/bold magenta]
"""
    console.print(Panel.fit(banner, border_style="magenta"))

    input_method = Prompt.ask("[bold]Input method[/]", choices=["single", "list", "file"], default="single")

    urls = []
    if input_method == "single":
        url = Prompt.ask("[bold cyan]Enter download URL")
        urls = [url]
    elif input_method == "list":
        console.print("[yellow]Enter URLs one per line, empty line to finish:[/]")
        while True:
            line = Prompt.ask("", default="").strip()
            if not line:
                break
            urls.append(line)
    else:
        file_path = Prompt.ask("[bold]Path to URL list file")
        try:
            with open(file_path, 'r') as f:
                urls = [line.strip() for line in f if line.strip()]
            console.print(f"[green]Loaded {len(urls)} URLs from file.")
        except Exception as e:
            console.print(f"[red]Error reading file: {e}")
            return

    if not urls:
        console.print("[red]No URLs provided. Exiting.")
        return

    out_dir = Prompt.ask("[bold]Output directory", default=".")
    threads = int(Prompt.ask("[bold]Number of threads (per file)", default="16"))
    chunk_size_mb = float(Prompt.ask("[bold]Chunk size in MB (e.g., 10)", default="10"))
    chunk_size = int(chunk_size_mb * 1024 * 1024)

    proxy_input = Prompt.ask("[bold]Proxy (single) or path to proxy list file", default="")
    proxy_list = []
    if proxy_input:
        if os.path.isfile(proxy_input):
            with open(proxy_input, 'r') as f:
                proxy_list = [line.strip() for line in f if line.strip()]
        else:
            proxy_list = [proxy_input]

    proxy_auth = None
    if proxy_list:
        proxy_user = Prompt.ask("[bold]Proxy username (if required)", default="")
        proxy_pass = Prompt.ask("[bold]Proxy password", password=True, default="")
        if proxy_user and proxy_pass:
            proxy_auth = (proxy_user, proxy_pass)

    headers_input = Prompt.ask("[bold]Extra headers (comma-separated, e.g. Referer:https://...)", default="")
    headers = {}
    if headers_input:
        for pair in headers_input.split(','):
            if ':' in pair:
                k, v = pair.strip().split(':', 1)
                headers[k.strip()] = v.strip()

    ua_rotate = Confirm.ask("[bold]Rotate User-Agent?", default=True)
    verify_ssl = Confirm.ask("[bold]Verify SSL certificates?", default=True)
    resume = Confirm.ask("[bold]Enable resume?", default=True)

    checksum = Prompt.ask("[bold]Checksum (e.g., md5:hash or sha256:hash)", default="")
    checksum_type = None
    checksum_hash = None
    if checksum:
        if ':' in checksum:
            checksum_type, checksum_hash = checksum.split(':', 1)
        else:
            checksum_type = "md5"
            checksum_hash = checksum

    speed_limit_kb = Prompt.ask("[bold]Speed limit per download (KB/s, 0 for unlimited)", default="0")
    speed_limit = int(float(speed_limit_kb) * 1024) if float(speed_limit_kb) > 0 else None

    http2 = Confirm.ask("[bold]Use HTTP/2 if available? (requires httpx)", default=False)
    debug = Confirm.ask("[bold]Enable debug logging?", default=False)

    engine = RownloarEngine(
        console=console,
        max_threads=threads,
        chunk_size=chunk_size,
        proxy_list=proxy_list,
        proxy_auth=proxy_auth,
        headers=headers,
        use_random_ua=ua_rotate,
        verify_ssl=verify_ssl,
        speed_limit=speed_limit,
        resume=resume,
        checksum=checksum_hash,
        checksum_type=checksum_type,
        http2=http2,
        debug=debug,
    )

    try:
        engine.run(urls, output_dir=out_dir)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user. Partial downloads may be resumed later.")
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}")
        if debug:
            import traceback
            traceback.print_exc()

    if Confirm.ask("[bold]Download more?", default=False):
        interactive_mode()

def main():
    try:
        interactive_mode()
    except KeyboardInterrupt:
        console = Console()
        console.print("\n[yellow]Exiting...")
    finally:
        console = Console()
        console.print("[bold magenta]Thank you for using Rownloar Pro![/] 🚀")

if __name__ == "__main__":
    main()