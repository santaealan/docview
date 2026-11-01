# /// script
# requires-python = ">=3.10"
# dependencies = ["textual>=0.80", "markdownify>=0.13", "beautifulsoup4>=4.12"]
# ///
"""Read-only terminal preview of a Feishu doc or a local .md/.html file.

The target is read from the file named by $DOCVIEW_TARGET_FILE (written by
bin/docview). Touching that file forces a reload. Local files reload when they
change on disk; Feishu docs are polled and re-rendered when the revision changes.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import ContentSwitcher, DirectoryTree, Footer, Input, Static

FEISHU_POLL_SECONDS = 20
LOCAL_SUFFIXES = (".md", ".markdown", ".html", ".htm")


# ---------- loading ----------

def is_url(target: str) -> bool:
    return target.startswith(("http://", "https://"))


def fetch_feishu(url: str) -> tuple[str, str, str]:
    """Return (title, revision, content) via lark-cli."""
    r = subprocess.run(
        ["lark-cli", "docs", "+fetch", "--doc", url, "--doc-format", "markdown"],
        capture_output=True, text=True, timeout=90,
    )
    try:
        # lark-cli prints the result JSON to stdout, but an error JSON to stderr.
        data = json.loads(r.stdout if r.stdout.strip() else r.stderr)
    except json.JSONDecodeError:
        raise RuntimeError((r.stderr or r.stdout or f"lark-cli exit {r.returncode}").strip()[:500])
    if not data.get("ok"):
        err = data.get("error") or {}
        raise RuntimeError(err.get("message") or json.dumps(err or data, ensure_ascii=False)[:300])
    doc = data["data"]["document"]
    content = doc.get("content", "")
    m = re.search(r"<title>(.*?)</title>", content, re.S)
    title = m.group(1).strip() if m else url
    content = re.sub(r"<title>.*?</title>", "", content, count=1, flags=re.S)
    return title, str(doc.get("revision_id", "")), content


class _KeepTables(MarkdownConverter):
    """Leave <table> as raw HTML so render() can draw it with the table renderer."""

    def convert_table(self, el, text, *args, **kwargs):
        return "\n\n" + str(el) + "\n\n"


def html_to_markdown(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for t in soup(["script", "style", "head", "noscript"]):
        t.decompose()
    md = _KeepTables(heading_style="ATX").convert_soup(soup.body or soup)
    return title, md


def load_local(path: str) -> tuple[str, str]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    if path.lower().endswith((".html", ".htm")):
        title, md = html_to_markdown(raw)
        return title or Path(path).name, md
    return Path(path).name, raw


# ---------- rendering ----------

def _attr(tag: str, name: str) -> str:
    m = re.search(rf'{name}="([^"]*)"', tag)
    return m.group(1) if m else ""


def inline_html_to_md(s: str) -> str:
    """Turn the inline HTML that Feishu leaves inside markdown into markdown/plain text."""
    s = re.sub(r"<cite\b[^>]*>.*?</cite>", lambda m: f"[文档: {_attr(m.group(0), 'title')}]", s, flags=re.S)
    s = re.sub(r"<figure\b.*?</figure>",
               lambda m: f"[附件: {_attr(m.group(0), 'name') or _attr(m.group(0), 'alt') or '图片'}]", s, flags=re.S)
    s = re.sub(r"<img\b[^>]*>", lambda m: f"[图片: {_attr(m.group(0), 'alt') or _attr(m.group(0), 'src')}]", s)
    s = re.sub(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", s, flags=re.S)
    s = re.sub(r"</?(b|strong)>", "**", s)
    s = re.sub(r"</?(i|em)>", "*", s)
    s = re.sub(r"</?(del|s)>", "~~", s)
    s = re.sub(r"<br\s*/?>", "  \n", s)
    # Drop any remaining tags (e.g. <synced-source>) but keep their text.
    return re.sub(r"</?[a-zA-Z][\w-]*(\s[^<>]*)?/?>", "", s)


def cell_text(td) -> str:
    for br in td.find_all("br"):
        br.replace_with("\n")
    for c in td.find_all("cite"):
        c.replace_with(f"[文档: {c.get('title', '')}]")
    for f in td.find_all(["figure", "img"]):
        src = f if f.name == "img" else (f.find("source") or f.find("img") or f)
        f.replace_with(f"[附件: {src.get('name') or src.get('alt') or '图片'}]")
    for lst in td.find_all(["ol", "ul"]):
        for i, li in enumerate(lst.find_all("li", recursive=False), 1):
            li.insert(0, f"{i}." if lst.name == "ol" else "•")
            li.append("\n")
    for p in td.find_all(["p", "div"]):
        p.append("\n")
    lines = [ln.strip() for ln in td.get_text().splitlines()]
    return "\n".join(ln for ln in lines if ln)


def table_renderable(html: str) -> Table:
    table = BeautifulSoup(html, "html.parser").find("table")
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    rows, has_header = [], False
    spans: dict[int, int] = {}  # column -> rows still covered by a rowspan above
    for idx, tr in enumerate(trs):
        cells = tr.find_all(["td", "th"], recursive=False)
        if idx == 0 and cells and all(c.name == "th" for c in cells):
            has_header = True
        row, col = [], 0
        for td in cells:
            while spans.get(col):
                row.append("")
                spans[col] -= 1
                col += 1
            rs = int(td.get("rowspan") or 1)
            cs = int(td.get("colspan") or 1)
            row.append(cell_text(td))
            row.extend([""] * (cs - 1))
            if rs > 1:
                for c in range(col, col + cs):
                    spans[c] = rs - 1
            col += cs
        while spans.get(col):
            row.append("")
            spans[col] -= 1
            col += 1
        rows.append(row)

    ncols = max((len(r) for r in rows), default=0)
    t = Table(show_header=has_header, show_lines=True, expand=True)
    head = rows.pop(0) if has_header else []
    for i in range(ncols):
        t.add_column(head[i] if i < len(head) else "", overflow="fold")
    for r in rows:
        t.add_row(*(r + [""] * (ncols - len(r))))
    return t


def render(content: str) -> Group:
    parts = re.split(r"(<table\b.*?</table>)", content, flags=re.S | re.I)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(table_renderable(part))
            out.append(Text(""))
        elif part.strip():
            out.append(Markdown(inline_html_to_md(part), hyperlinks=True))
    return Group(*out)


# ---------- app ----------

def mtime(path: str):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class PickerTree(DirectoryTree):
    """Directory tree showing only folders and previewable files."""

    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".")
                and (p.is_dir() or p.suffix.lower() in LOCAL_SUFFIXES)]


class DocView(App):
    CSS = """
    #bar { height: 1; background: $primary-background; padding: 0 1; }
    #body { padding: 0 1; }
    #picker Input { margin: 1 0 0 0; }
    """
    BINDINGS = [
        ("q", "quit", "退出"),
        ("o", "picker", "打开其他"),
        ("u", "parent", "上级目录"),
        ("r", "reload", "刷新"),
        ("j", "scroll(1)", "下"),
        ("k", "scroll(-1)", "上"),
        ("space", "page(1)", "翻页"),
        ("b", "page(-1)", ""),
        ("g", "top", "顶部"),
        ("G", "bottom", "底部"),
    ]

    def __init__(self, target_file: str):
        super().__init__()
        self.target_file = target_file
        self.target_mtime = None
        self.target = ""
        self.shown_target = ""
        self.title_text = ""
        self.revision = None
        self.file_mtime = None
        self.next_poll = 0.0
        self.loading = False

    def compose(self) -> ComposeResult:
        yield Static("加载中…", id="bar")
        with ContentSwitcher(initial="body", id="views"):
            with Vertical(id="picker"):
                yield Input(placeholder="输入路径（文件或目录）或飞书链接，回车打开；Tab 切到下方文件树")
                yield PickerTree(os.getcwd())
            with VerticalScroll(id="body"):
                yield Static("", id="doc")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#body").focus()
        self.set_interval(1, self.tick)
        self.tick()

    # ----- start page -----

    def picking(self) -> bool:
        return self.query_one("#views", ContentSwitcher).current == "picker"

    def show_view(self, name: str) -> None:
        self.query_one("#views", ContentSwitcher).current = name
        if name == "picker":
            tree = self.query_one(PickerTree)
            tree.focus()
            self.title_text = ""
            self.set_bar(f"选择要打开的文件 · {tree.path}")
        else:
            self.query_one("#body").focus()
        self.refresh_bindings()

    def check_action(self, action: str, parameters) -> bool | None:
        picker_only = {"parent"}
        doc_only = {"picker", "reload", "scroll", "page", "top", "bottom"}
        if action in picker_only:
            return self.picking()
        if action in doc_only:
            return not self.picking()
        return True

    def set_target(self, target: str) -> None:
        """Hand the target to tick() through the target file, same as bin/docview does."""
        tmp = self.target_file + ".tmp"
        Path(tmp).write_text(target + "\n")
        os.replace(tmp, self.target_file)
        self.tick()

    def open_input(self, value: str) -> None:
        value = value.strip()
        if not value:
            return
        if is_url(value):
            self.set_target(value)
            return
        tree = self.query_one(PickerTree)
        p = Path(value).expanduser()
        if not p.is_absolute():
            p = Path(tree.path) / p
        p = p.resolve()
        if p.is_dir():
            tree.path = p
            self.set_bar(f"选择要打开的文件 · {p}")
        elif p.is_file() and p.suffix.lower() in LOCAL_SUFFIXES:
            self.set_target(str(p))
        elif p.is_file():
            self.set_bar(f"只支持 .md/.markdown/.html/.htm: {p}", error=True)
        else:
            self.set_bar(f"路径不存在: {p}", error=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value
        event.input.value = ""
        self.open_input(value)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.set_target(str(event.path))

    def action_picker(self) -> None:
        self.set_target("")

    def action_parent(self) -> None:
        tree = self.query_one(PickerTree)
        tree.path = Path(tree.path).resolve().parent
        self.set_bar(f"选择要打开的文件 · {tree.path}")

    # ----- loading -----

    def set_bar(self, msg: str, error: bool = False) -> None:
        bar = self.query_one("#bar", Static)
        label = f"{self.title_text} · " if self.title_text else ""
        bar.update(Text(label + msg, style="bold red" if error else ""))

    def tick(self) -> None:
        if self.loading:
            return
        m = mtime(self.target_file)
        if m != self.target_mtime:
            self.target_mtime = m
            try:
                self.target = Path(self.target_file).read_text().strip()
            except OSError:
                self.target = ""
            if not self.target or os.path.isdir(self.target):
                # Empty target: start page as it was; directory target: start page rooted there.
                if self.target:
                    self.query_one(PickerTree).path = self.target
                self.shown_target = ""
                self.show_view("picker")
                return
            self.start_load(force=True)
        elif self.picking():
            return
        elif is_url(self.target):
            if time.monotonic() >= self.next_poll:
                self.start_load(force=False)
        elif mtime(self.target) != self.file_mtime:
            self.start_load(force=True)

    def start_load(self, force: bool) -> None:
        self.loading = True
        self.load(self.target, force)

    @work(thread=True, exclusive=True)
    def load(self, target: str, force: bool) -> None:
        try:
            if is_url(target):
                title, rev, content = fetch_feishu(target)
                same = target == self.shown_target and rev == self.revision
                if same and not force:
                    self.call_from_thread(self.loaded, None, title, rev, target, None)
                    return
                fm = None
            else:
                fm = mtime(target)
                title, content = load_local(target)
                rev = None
            self.call_from_thread(self.loaded, render(content), title, rev, target, fm)
        except Exception as e:  # show any load/render failure in the bar, keep last good view
            self.call_from_thread(self.failed, target, e)

    def loaded(self, renderable, title, rev, target, fm) -> None:
        self.loading = False
        if target != self.target:  # user switched away while this was loading
            return
        if self.picking():
            self.show_view("body")
        self.next_poll = time.monotonic() + FEISHU_POLL_SECONDS
        self.title_text, self.revision, self.file_mtime = title, rev, fm
        if renderable is not None:
            self.query_one("#doc", Static).update(renderable)
            if target != self.shown_target:
                self.query_one("#body").scroll_home(animate=False)
            self.shown_target = target
        src = "飞书" if is_url(target) else "本地"
        rev_s = f" · rev {rev}" if rev else ""
        self.set_bar(f"{src}{rev_s} · 更新于 {time.strftime('%H:%M:%S')}")

    def failed(self, target, err) -> None:
        self.loading = False
        if target != self.target:  # user switched away while this was loading
            return
        if self.picking():
            self.show_view("body")
        self.next_poll = time.monotonic() + FEISHU_POLL_SECONDS
        if not is_url(target):
            self.file_mtime = mtime(target)  # don't retry every second on a broken file
        if target != self.shown_target:
            # A new target failed: don't leave the previous document on screen under it.
            self.query_one("#doc", Static).update("")
            self.title_text, self.shown_target, self.revision = target, target, None
        self.set_bar("加载失败: " + " ".join(str(err).split()), error=True)

    def action_reload(self) -> None:
        if self.target and not self.loading:
            self.start_load(force=True)

    def action_scroll(self, n: int) -> None:
        self.query_one("#body").scroll_relative(y=n, animate=False)

    def action_page(self, n: int) -> None:
        body = self.query_one("#body")
        body.scroll_relative(y=n * max(body.size.height - 2, 1), animate=False)

    def action_top(self) -> None:
        self.query_one("#body").scroll_home(animate=False)

    def action_bottom(self) -> None:
        self.query_one("#body").scroll_end(animate=False)


if __name__ == "__main__":
    tf = os.environ.get("DOCVIEW_TARGET_FILE") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not tf:
        sys.exit("usage: DOCVIEW_TARGET_FILE=<file containing url/path> docview.py")
    try:
        DocView(tf).run()
    finally:
        Path(tf).unlink(missing_ok=True)  # so `docview --refresh` only sees running viewers
