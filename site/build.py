#!/usr/bin/env python3
"""Build the omaorchestra website (GitHub Pages) from the README and docs/.

    site/build.py                 # build into _site/
    site/build.py --out DIR       # somewhere else
    site/build.py --serve         # build, then serve on http://localhost:8000
    site/build.py --offline       # skip the GitHub API; the releases page says so

The docs are the source of truth: every docs/*.md page (ROADMAP.md is private
and left out) and the README's Install, Setup and Supported versions sections
are rendered as they are, with heading anchors that match GitHub's so links
into either keep working. Releases come from the GitHub API at build time;
set GITHUB_TOKEN (the Pages workflow does) to avoid the anonymous rate limit.

Every link in the output is relative, so the site works under
/omaorchestra/ on github.io, on a custom domain, or opened from disk.

Needs Python-Markdown and Pygments:  pip install -r site/requirements.txt
"""

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath

import markdown
from markdown.extensions.toc import TocExtension

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
REPO = "njcurtis3/omaorchestra"
GITHUB = f"https://github.com/{REPO}"
SITE_URL = os.environ.get("SITE_URL", "https://njcurtis3.github.io/omaorchestra/").rstrip("/") + "/"
DESCRIPTION = ("Conduct your AI coding agents on Omarchy: see who is working, "
               "who needs you, and jump straight there.")

# The docs, in reading order. The slug is the page's URL: docs/<slug>/.
NAV = [
    ("Get started", [
        ("install", None, "Install"),
    ]),
    ("Using omaorchestra", [
        ("guide", "docs/guide.md", None),
        ("fleets", "docs/fleets.md", None),
        ("remote", "docs/remote.md", None),
        ("providers", "docs/providers.md", None),
        ("mcp", "docs/mcp.md", None),
    ]),
    ("Reference", [
        ("commands", "docs/commands.md", None),
        ("troubleshooting", "docs/troubleshooting.md", None),
        ("security", "docs/security.md", None),
    ]),
    ("Project", [
        ("development", "docs/development.md", None),
        ("architecture", "docs/architecture.md", None),
    ]),
]

# Docs pages that carry a status badge in the sidebar and page header.
BADGES = {"fleets": "Experimental"}

# README sections that make up the Install page, in order.
README_SECTIONS = ("Install", "Setup")


# ------------------------------------------------------------------ helpers

def slug(heading):
    """GitHub's anchor for a heading: lower case, only letters, digits,
    spaces, hyphens and underscores kept, spaces as hyphens. Same rule as
    scripts/doc-sections, so the sections lines' anchors resolve here too."""
    text = heading.strip().lower()
    text = "".join(c for c in text if c in " -_" or unicodedata.category(c)[0] in "LN")
    return text.replace(" ", "-")


def github_slugify(value, separator):
    # Headings reach the toc extension with inline markup already rendered.
    return slug(re.sub(r"<[^>]+>", "", html.unescape(value)))


def esc(text):
    return html.escape(str(text), quote=True)


def strip_tags(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%B %-d, %Y")


# ------------------------------------------------------------------ markdown

ALERT = re.compile(r"^> \[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*\n((?:>.*\n?)*)", re.M)
ALERT_TITLES = {"NOTE": "Note", "TIP": "Tip", "IMPORTANT": "Important",
                "WARNING": "Warning", "CAUTION": "Caution"}


def alerts_to_callouts(text):
    """GitHub's `> [!WARNING]` blockquotes become styled callouts."""
    def repl(m):
        kind = m.group(1)
        body = re.sub(r"^> ?", "", m.group(2), flags=re.M)
        return (f'<div class="callout callout-{kind.lower()}" markdown="1">\n'
                f'<p class="callout-title">{ALERT_TITLES[kind]}</p>\n\n{body}\n</div>\n')
    return ALERT.sub(repl, text)


def nest_lists(text):
    """GitHub nests a list item indented by two spaces; Python-Markdown wants
    four. Double list-item indents, leaving fenced code alone."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            m = re.match(r"^( +)([-*+] |\d+[.)] )", line)
            if m and len(m.group(1)) % 4:
                line = " " * (len(m.group(1)) * 2) + line[len(m.group(1)):]
        out.append(line)
    return "\n".join(out)


def prepare(text):
    # The site shows its own table of contents; the "Sections:" line is for GitHub.
    text = re.sub(r"<!-- sections -->.*?<!-- /sections -->\n*", "", text, flags=re.S)
    return alerts_to_callouts(nest_lists(text))


def split_title(text):
    m = re.match(r"\s*# (.+)\n", text)
    if not m:
        return None, text
    return m.group(1).strip(), text[m.end():]


class Renderer:
    """Markdown to HTML with GitHub-compatible anchors, and every relative link
    rewritten for where the page will live."""

    def __init__(self, pages):
        # Repo path (e.g. "docs/guide.md") -> site path (e.g. "docs/guide/").
        self.pages = pages

    def render(self, text, source, out_path):
        md = markdown.Markdown(extensions=[
            "tables", "fenced_code", "attr_list", "md_in_html", "sane_lists",
            TocExtension(slugify=github_slugify, toc_depth="2-3", separator="-"),
            "codehilite",
        ], extension_configs={
            "codehilite": {"css_class": "hl", "guess_lang": False},
        })
        body = md.convert(prepare(text))
        body = self.rewrite_links(body, source, out_path)
        body = self.decorate(body)
        return body, md.toc_tokens

    def resolve(self, target, source, out_path):
        """Map a link found in `source` (a repo path) to a URL relative to
        `out_path` (a site path like "docs/guide/")."""
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^//", target, re.I):
            return target
        path, _, frag = target.partition("#")
        frag = f"#{frag}" if frag else ""
        if not path:
            return target
        repo_path = str(PurePosixPath(os.path.normpath(PurePosixPath(source).parent / path)))
        is_dir = path.endswith("/")

        if repo_path == "README.md":
            # The docs send readers to the README for installing and setting
            # up, which on the site is the Install page.
            if frag in ("", "#install", "#setup", "#uninstall", "#supported-versions"):
                return rel(out_path, "docs/install/") + frag
            return rel(out_path, "") + frag
        if repo_path in self.pages:
            return rel(out_path, self.pages[repo_path]) + frag
        for folder in ("docs/screenshots/", "docs/assets/"):
            if repo_path.startswith(folder):
                return rel(out_path, "assets/" + repo_path[len(folder):]) + frag
        kind = "tree" if is_dir or (ROOT / repo_path).is_dir() else "blob"
        return f"{GITHUB}/{kind}/main/{repo_path}{'/' if is_dir else ''}{frag}"

    def rewrite_links(self, body, source, out_path):
        def repl(m):
            attr, quote, target = m.group(1), m.group(2), html.unescape(m.group(3))
            return f"{attr}={quote}{esc(self.resolve(target, source, out_path))}{quote}"
        return re.sub(r'\b(href|src)=(["\'])(.*?)\2', repl, body)

    @staticmethod
    def decorate(body):
        # Headings get a hover link to themselves.
        body = re.sub(
            r'<(h[234]) id="([^"]+)">(.*?)</\1>',
            lambda m: (f'<{m.group(1)} id="{m.group(2)}">{m.group(3)}'
                       f'<a class="anchor" href="#{m.group(2)}" aria-label="Link to this section">#</a>'
                       f'</{m.group(1)}>'),
            body)
        # Wide tables scroll inside their own box instead of the page.
        body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
        # External links open in the same tab but are marked for styling.
        body = re.sub(r'<a href="(https?://[^"]+)"', r'<a class="ext" href="\1"', body)
        # Images are lazy and never overflow.
        body = body.replace("<img ", '<img loading="lazy" ')
        return body


def rel(from_dir, to):
    """Relative URL from the page at site path `from_dir` ("" or "docs/x/")
    to site path `to` ("" for home, "docs/y/", "assets/logo.svg")."""
    depth = len([p for p in from_dir.split("/") if p])
    return ("../" * depth) + to if (depth or to) else "./"


# ------------------------------------------------------------------ data

def fetch_releases(offline):
    if offline:
        return None
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "omaorchestra-site"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            releases = json.load(r)
    except Exception as e:
        # Locally, gh is usually signed in even when the anonymous limit is hit.
        try:
            out = subprocess.run(["gh", "api", f"repos/{REPO}/releases?per_page=50"],
                                 capture_output=True, check=True, timeout=20).stdout
            releases = json.loads(out)
        except Exception:
            print(f"warning: could not fetch releases ({e}); building without them", file=sys.stderr)
            return None
    return [r for r in releases if not r.get("draft")]


def readme_sections():
    """The README's Install and Setup sections as one Markdown document, with
    the <details> blocks opened out into headings for a docs page."""
    text = (ROOT / "README.md").read_text()
    chunks = re.split(r"^## ", text, flags=re.M)
    wanted = {c.split("\n", 1)[0].strip(): c for c in chunks[1:]}
    out = []
    for name in README_SECTIONS:
        body = wanted[name].split("\n", 1)[1]
        body = re.sub(r'<a id="[^"]+"></a>\n', "", body)
        body = re.sub(r"<details(?: open)?>\s*<summary>\s*(.*?)\s*</summary>\s*<br\s*/?>",
                      lambda m: f"### {m.group(1).strip()}\n", body, flags=re.S)
        body = body.replace("</details>", "")
        out.append(f"## {name}\n{body}")
    return "\n".join(out)


# ------------------------------------------------------------------ templates

def load(name):
    return (SITE / "templates" / name).read_text()


def fill(template, **values):
    """{{name}} placeholders. Unknown names are a bug, not a blank."""
    def repl(m):
        key = m.group(1)
        if key not in values:
            raise KeyError(f"template placeholder {{{{{key}}}}} has no value")
        return str(values[key])
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", repl, template)


def nav_html(here, base):
    parts = []
    for group, items in NAV:
        parts.append(f'<div class="nav-group"><p class="nav-heading">{esc(group)}</p><ul>')
        for page_slug, source, title in items:
            title = title or PAGE_TITLES[page_slug]
            badge = (f' <span class="badge badge-sm">{esc(BADGES[page_slug])}</span>'
                     if page_slug in BADGES else "")
            current = ' aria-current="page"' if page_slug == here else ""
            parts.append(f'<li><a href="{base}docs/{page_slug}/"{current}>{esc(title)}{badge}</a></li>')
        parts.append("</ul></div>")
    parts.append(f'<div class="nav-group"><ul><li><a href="{base}releases/"'
                 f'{" aria-current=page" if here == "releases" else ""}>Releases</a></li></ul></div>')
    return "\n".join(parts)


def toc_html(tokens):
    items = []
    for t in tokens:
        items.append(f'<li><a href="#{t["id"]}">{strip_tags(t["name"])}</a>')
        if t["children"]:
            items.append('<ul>' + "".join(
                f'<li><a href="#{c["id"]}">{strip_tags(c["name"])}</a></li>' for c in t["children"]) + '</ul>')
        items.append("</li>")
    if not items:
        return ""
    return ('<nav class="toc" aria-label="On this page"><p class="toc-heading">On this page</p>'
            f'<ul>{"".join(items)}</ul></nav>')


def page(out_dir, *, title, description, body, active, latest, canonical, main_class=""):
    base = rel(out_dir, "")
    full_title = "omaorchestra" if title is None else f"{title} · omaorchestra"
    return fill(load("layout.html"),
                base=base,
                title=esc(full_title),
                description=esc(description),
                canonical=esc(SITE_URL + canonical),
                og_image=esc(SITE_URL + "assets/social-preview.png"),
                version=esc(latest["version"]) if latest else "",
                nav_docs=' aria-current="page"' if active == "docs" else "",
                nav_install=' aria-current="page"' if active == "install" else "",
                nav_releases=' aria-current="page"' if active == "releases" else "",
                main_class=main_class,
                body=body,
                year=datetime.now().year,
                github=GITHUB)


def doc_page(out_dir, *, page_slug, title, lede, body, toc, source, latest, extra_top=""):
    base = rel(out_dir, "")
    flat = [s for _, items in NAV for s, _, _ in items]
    i = flat.index(page_slug)
    prev_slug = flat[i - 1] if i > 0 else None
    next_slug = flat[i + 1] if i + 1 < len(flat) else None
    pager = '<nav class="pager" aria-label="Previous and next page">'
    pager += (f'<a class="pager-prev" href="{base}docs/{prev_slug}/"><span>Previous</span>'
              f'{esc(PAGE_TITLES[prev_slug])}</a>' if prev_slug else "<span></span>")
    pager += (f'<a class="pager-next" href="{base}docs/{next_slug}/"><span>Next</span>'
              f'{esc(PAGE_TITLES[next_slug])}</a>' if next_slug else "<span></span>")
    pager += "</nav>"
    badge = (f'<span class="badge">{esc(BADGES[page_slug])}</span>' if page_slug in BADGES else "")
    group = next(g for g, items in NAV if any(s == page_slug for s, _, _ in items))
    inner = fill(load("doc.html"),
                 nav=nav_html(page_slug, base),
                 group=esc(group),
                 title=esc(title),
                 badge=badge,
                 lede=f'<p class="lede">{lede}</p>' if lede else "",
                 extra_top=extra_top,
                 body=body,
                 toc=toc,
                 pager=pager,
                 edit_url=f"{GITHUB}/edit/main/{source}")
    return page(out_dir, title=title, description=strip_tags(lede) if lede else DESCRIPTION,
                body=inner, active="install" if page_slug == "install" else "docs",
                latest=latest, canonical=out_dir, main_class="main-docs")


PAGE_TITLES = {}


# ------------------------------------------------------------------ build

def first_paragraph(html_body):
    """Split a rendered doc into its opening paragraph (the page's lede) and
    the rest, when it opens with one."""
    m = re.match(r"\s*<p>(.*?)</p>\s*", html_body, re.S)
    if not m:
        return "", html_body
    return m.group(1), html_body[m.end():]


def search_entries(url, page_title, body):
    """One search entry per section: the page itself, then each h2/h3."""
    entries = []
    parts = re.split(r'(<h[23] id="[^"]+">.*?</h[23]>)', body, flags=re.S)
    current = {"title": page_title, "page": page_title, "url": url, "text": ""}
    for part in parts:
        m = re.match(r'<h[23] id="([^"]+)">(.*?)<a class="anchor"', part, re.S)
        if m:
            entries.append(current)
            current = {"title": strip_tags(m.group(2)), "page": page_title,
                       "url": f"{url}#{m.group(1)}", "text": ""}
        else:
            current["text"] += " " + strip_tags(re.sub(r'<div class="hl">.*?</div>', " ", part, flags=re.S))
    entries.append(current)
    for e in entries:
        e["text"] = e["text"].strip()[:600]
    return entries


def build(out, offline):
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # ---- which repo file becomes which page
    pages = {}
    for _, items in NAV:
        for page_slug, source, title in items:
            if source:
                pages[source] = f"docs/{page_slug}/"
                PAGE_TITLES[page_slug] = split_title((ROOT / source).read_text())[0]
            else:
                PAGE_TITLES[page_slug] = title
    renderer = Renderer(pages)

    releases = fetch_releases(offline)
    latest = None
    if releases:
        r = next((r for r in releases if not r.get("prerelease")), releases[0])
        asset = next((a for a in r["assets"] if a["name"].endswith(".pkg.tar.zst")), None)
        latest = {
            "version": r["tag_name"].lstrip("v"),
            "tag": r["tag_name"],
            "name": r["name"] or r["tag_name"],
            "date": human_date(r["published_at"]),
            "url": r["html_url"],
            "asset": asset,
        }

    search = []

    # ---- static assets
    shutil.copytree(SITE / "static", out / "static")
    (out / "assets").mkdir()
    for folder in ("docs/assets", "docs/screenshots"):
        for f in (ROOT / folder).iterdir():
            if f.is_file():
                shutil.copy2(f, out / "assets" / f.name)
    (out / ".nojekyll").write_text("")

    # ---- docs
    for _, items in NAV:
        for page_slug, source, _ in items:
            if not source:
                continue
            out_dir = f"docs/{page_slug}/"
            title, text = split_title((ROOT / source).read_text())
            body, tokens = renderer.render(text, source, out_dir)
            lede, body = first_paragraph(body)
            write(out / out_dir / "index.html", doc_page(
                out_dir, page_slug=page_slug, title=title, lede=lede, body=body,
                toc=toc_html(tokens), source=source, latest=latest))
            search += search_entries(out_dir, title, (f"<p>{lede}</p>" if lede else "") + body)

    # ---- install, from the README
    out_dir = "docs/install/"
    body, tokens = renderer.render(readme_sections(), "README.md", out_dir)
    write(out / out_dir / "index.html", doc_page(
        out_dir, page_slug="install", title="Install",
        lede=("One package for every Omarchy machine. Install it, run "
              "<code>omaorchestra setup</code>, and your agents show up in the bar."),
        body=body, toc=toc_html(tokens), source="README.md", latest=latest,
        extra_top=release_card(latest, rel(out_dir, ""))))
    search += search_entries(out_dir, "Install", body)

    # ---- releases
    out_dir = "releases/"
    write(out / out_dir / "index.html", page(
        out_dir, title="Releases", description="Every omaorchestra release, with notes and downloads.",
        body=releases_html(releases, renderer, out_dir), active="releases", latest=latest,
        canonical=out_dir))

    # ---- home
    home = fill(load("home.html"),
                base="./",
                version=esc(latest["version"]) if latest else "",
                download_url=esc(latest["asset"]["browser_download_url"] if latest and latest["asset"] else f"{GITHUB}/releases/latest"),
                asset_name=esc(latest["asset"]["name"] if latest and latest["asset"] else "omaorchestra-*-any.pkg.tar.zst"),
                release_url=esc(latest["url"] if latest else f"{GITHUB}/releases"),
                github=GITHUB)
    write(out / "index.html", page("", title=None, description=DESCRIPTION, body=home,
                                   active="home", latest=latest, canonical="", main_class="main-home"))

    # ---- 404: GitHub Pages serves it from any depth, so links are absolute.
    notfound = page("", title="Page not found", description=DESCRIPTION,
                    body=load("404.html"), active="", latest=latest, canonical="404.html")
    abs_base = "/" + SITE_URL.split("://", 1)[1].split("/", 1)[1] if SITE_URL.count("/") > 3 else "/"
    notfound = re.sub(r'(href|src)="\./', rf'\1="{abs_base}', notfound)
    write(out / "404.html", notfound)

    # ---- search index, sitemap, robots
    (out / "search.json").write_text(json.dumps(search, separators=(",", ":")))
    urls = ["", "docs/install/", "releases/"] + [f"docs/{s}/" for _, it in NAV for s, src, _ in it if src]
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{SITE_URL}{u}</loc></url>\n" for u in urls) + "</urlset>\n")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}sitemap.xml\n")

    print(f"built {len(urls)} pages into {out}"
          + ("" if releases else " (no release data)"))


def release_card(latest, base):
    if not latest or not latest["asset"]:
        return (f'<div class="release-card"><p>Download the package from '
                f'<a href="{GITHUB}/releases/latest">the latest release</a>.</p></div>')
    a = latest["asset"]
    sha = (a.get("digest") or "").removeprefix("sha256:")
    sha_row = (f'<div class="release-sha"><span>SHA-256</span>'
               f'<code class="copyable" data-copy="{esc(sha)}">{esc(sha)}</code></div>') if sha else ""
    return f"""
<div class="release-card">
  <div class="release-card-head">
    <div>
      <p class="eyebrow">Latest release</p>
      <p class="release-card-title">{esc(latest["name"])}</p>
      <p class="release-card-meta">{esc(latest["date"])} · <a href="{base}releases/">All releases</a></p>
    </div>
    <a class="button button-primary" href="{esc(a["browser_download_url"])}">
      <svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M2.5 13.5h11"/></svg>
      Download
    </a>
  </div>
  <div class="release-file">
    <code>{esc(a["name"])}</code><span>{human_size(a["size"])}</span>
  </div>
  {sha_row}
</div>"""


def releases_html(releases, renderer, out_dir):
    head = ('<header class="page-head"><p class="eyebrow">Changelog</p><h1>Releases</h1>'
            '<p class="lede">Every omaorchestra release, newest first. Each one is a single package for '
            'every Omarchy machine; install it with <code>sudo pacman -U</code> and run '
            f'<code>omaorchestra setup</code> again. See <a href="{rel(out_dir, "docs/install/")}">Install</a>.</p></header>')
    if releases is None:
        return (f'<div class="releases">{head}<div class="callout callout-note"><p class="callout-title">Note</p>'
                f'<p>Release notes were not available when this page was built. See them on '
                f'<a href="{GITHUB}/releases">GitHub</a>.</p></div></div>')
    items = []
    for i, r in enumerate(releases):
        body, _ = renderer.render(r.get("body") or "", "README.md", out_dir)
        # Every release has a "What's new"; keep anchors unique on one page.
        tag = re.sub(r"[^a-z0-9-]", "-", r["tag_name"].lower())
        body = re.sub(r'(id|href)="#?([^"]+)"', lambda m: (
            f'{m.group(1)}="{"#" if m.group(1) == "href" else ""}{tag}-{m.group(2)}"'
            if m.group(1) == "id" or m.group(0).startswith('href="#') else m.group(0)), body)
        assets = "".join(
            f'<li><a href="{esc(a["browser_download_url"])}"><code>{esc(a["name"])}</code></a>'
            f'<span>{human_size(a["size"])}</span>'
            + (f'<code class="sha copyable" data-copy="{esc(a["digest"].removeprefix("sha256:"))}" '
               f'title="SHA-256, click to copy">sha256:{esc(a["digest"].removeprefix("sha256:")[:16])}…</code>'
               if a.get("digest") else "")
            + "</li>" for a in r["assets"])
        tags = ""
        if i == 0:
            tags += '<span class="badge badge-accent">Latest</span>'
        if r.get("prerelease"):
            tags += '<span class="badge">Pre-release</span>'
        items.append(f"""
<article class="release" id="{esc(r["tag_name"])}">
  <aside class="release-side">
    <a class="release-tag" href="#{esc(r["tag_name"])}">{esc(r["tag_name"])}</a>
    <time datetime="{esc(r["published_at"])}">{esc(human_date(r["published_at"]))}</time>
  </aside>
  <div class="release-main">
    <h2>{esc(r["name"] or r["tag_name"])} {tags}</h2>
    {f'<ul class="release-assets">{assets}</ul>' if assets else ''}
    <div class="prose">{body}</div>
    <p class="release-link"><a class="ext" href="{esc(r["html_url"])}">View on GitHub</a></p>
  </div>
</article>""")
    return f'<div class="releases">{head}{"".join(items)}</div>'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--offline", action="store_true", help="skip the GitHub API")
    ap.add_argument("--serve", action="store_true", help="serve the result on localhost:8000")
    args = ap.parse_args()
    build(args.out.resolve(), args.offline)
    if args.serve:
        import functools
        import http.server
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(args.out))
        print("serving on http://localhost:8000/")
        http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler).serve_forever()


if __name__ == "__main__":
    main()
