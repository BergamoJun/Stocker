"""Extract only explicitly selected official narrative; never execute HTML."""

from datetime import datetime
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

from research.rights import as_utc

BASE = Path(__file__).resolve().parent


def policy(clock):
    value = json.loads((BASE / "text_policy.json").read_text(encoding="utf-8"))
    if not as_utc(value["reviewed_at"]) <= as_utc(clock()) < as_utc(value["review_due_at"]):
        raise ValueError("TEXT_SOURCE_POLICY_NOT_CURRENT")
    if value["monthly_data_budget_usd"] != 0 or value["external_model_calls_enabled"] or value["model_training_enabled"]:
        raise ValueError("TEXT_SCOPE_CHANGED")
    return value


def approved_url(url, source, *, auxiliary=False):
    parsed = urlsplit(url)
    setting = json.loads((BASE / "text_policy.json").read_text(encoding="utf-8"))["sources"][source]
    if parsed.scheme != "https" or parsed.netloc != setting["host"] or parsed.fragment or any(ord(c) < 33 for c in url) or "%" in url:
        raise ValueError("UNAPPROVED_TEXT_ORIGIN")
    if auxiliary and url in {setting["feed_url"], setting["policy_url"], "https://" + setting["host"] + "/robots.txt"}:
        return url
    if source == "federal_reserve_board" and not parsed.query and re.fullmatch(r"/newsevents/pressreleases/monetary\d{8}[a-z]\.htm", parsed.path):
        return url
    query = parse_qs(parsed.query)
    if source == "us_eia" and parsed.path == "/todayinenergy/detail.php" and re.fullmatch(r"id=\d{1,9}", parsed.query) and set(query) == {"id"}:
        return url
    raise ValueError("UNAPPROVED_TEXT_PATH")


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def all(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.all()

    def text(self, *, blocked=None):
        blocked = blocked or {"script", "style", "noscript", "svg", "table", "figure", "img", "iframe", "nav", "footer", "form", "button"}
        if self.tag in blocked or "hidden" in self.attrs or self.attrs.get("aria-hidden") == "true":
            return ""
        return "".join(child.text(blocked=blocked) if isinstance(child, Node) else child for child in self.children)


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node("root")
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def clean(value):
    return re.sub(r"\s+", " ", value).strip()


def policy_hash(raw):
    # Policy paragraphs are stable across rotating HTML/script identifiers.
    # All paragraphs participate, including newly added usage restrictions.
    doc = Document(raw.decode("utf-8-sig"))
    text = "\n".join(clean(n.text()) for n in doc.root.all() if n.tag == "p" and clean(n.text()))
    if "public domain" not in text.lower() or len(text) < 100:
        raise ValueError("REVIEWED_POLICY_CONTENT_NOT_FOUND")
    return hashlib.sha256(text.encode()).hexdigest()


def article(raw, url, source, retrieved_at, settings):
    approved_url(url, source)
    if len(raw) > settings["maximum_response_bytes"] or b"\x00" in raw:
        raise ValueError("INVALID_TEXT_ENVELOPE")
    declaration = re.search(br'charset=[\s\"\']*([a-zA-Z0-9_-]+)', raw[:3000], re.I)
    encoding = declaration.group(1).decode("ascii").lower() if declaration else "utf-8-sig"
    if encoding not in {"utf-8", "utf-8-sig", "iso-8859-1", "windows-1252", "us-ascii"}:
        raise ValueError("UNSUPPORTED_TEXT_ENCODING")
    doc = Document(raw.decode(encoding))
    nodes = list(doc.root.all())
    if source == "federal_reserve_board":
        candidates = [n for n in nodes if n.attrs.get("id") == "article"] or [n for n in nodes if "col-xs-12 col-sm-8 col-md-8" in n.attrs.get("class", "") and "heading" not in n.attrs.get("class", "").split()]
    else:
        candidates = [n for n in nodes if n.attrs.get("id") in {"article", "article-content", "article_body"} or {"article-body", "tie-article"} & set(n.attrs.get("class", "").split())]
    # No whole-page fallback: template changes must not ingest navigation or ads.
    candidates = [n for n in candidates if any(x.tag == "p" for x in n.all())]
    if not candidates:
        raise ValueError("SUPPORTED_ARTICLE_CONTAINER_NOT_FOUND")
    container = min(candidates, key=lambda n: len(list(n.all())))
    owned = list(container.all())
    if any(any(token in (n.attrs.get("class", "") + " " + n.attrs.get("id", "")).lower() for token in ("copyright", "license", "rights-notice")) and clean(n.text()) for n in owned):
        raise ValueError("DOCUMENT_SPECIFIC_RIGHTS_NOT_CLEARED")
    paragraphs = []
    for n in owned:
        if n.tag != "p":
            continue
        ancestor = n.parent
        excluded = False
        while ancestor and ancestor is not container:
            if ancestor.tag in {"table", "figure", "aside", "nav", "footer", "script", "style"} or any(t in ancestor.attrs.get("class", "").lower() for t in ("source", "caption", "related", "contact", "share")):
                excluded = True
            ancestor = ancestor.parent
        if excluded or any(t in n.attrs.get("class", "").lower() for t in ("source", "caption", "contact", "release-time", "releasetime", "article__time")):
            continue
        text = clean(n.text())
        if text and not text.lower().startswith(("data source:", "source:", "principal contributor:", "for media inquiries", "last update:")):
            paragraphs.append(text)
    joined = "\n\n".join(paragraphs)
    if not 80 <= len(joined) <= settings["maximum_text_characters"] or not paragraphs or len(paragraphs) > settings["maximum_paragraphs"]:
        raise ValueError("INVALID_NARRATIVE_LENGTH")
    # Any indication that the selected narrative itself uses protected material
    # withholds the whole selection. Excluded figures/tables are never retained.
    if any(flag in joined.lower() for flag in settings["document_rights_flags"]):
        raise ValueError("DOCUMENT_SPECIFIC_RIGHTS_NOT_CLEARED")
    dates = set()
    for n in nodes:
        if n.tag == "meta" and (n.attrs.get("name", "").lower() in {"dc.date", "date", "datepublished"} or n.attrs.get("property") == "article:published_time"):
            text = n.attrs.get("content", "")[:10]
            try:
                dates.add(datetime.strptime(text, "%Y-%m-%d").date().isoformat())
            except ValueError:
                pass
    if not dates:
        date_nodes = owned + ([n for n in nodes if n.attrs.get("class") == "article__time" and "heading" in n.parent.attrs.get("class", "").split()] if source == "federal_reserve_board" else [])
        for n in date_nodes:
            if n.attrs.get("class") in {"dates", "date", "article-date", "article__time"} or n.tag == "time":
                text = clean(n.text())
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
                    try:
                        dates.add(datetime.strptime(text, fmt).date().isoformat())
                    except ValueError:
                        pass
    if len(dates) != 1 or next(iter(dates)) > as_utc(retrieved_at).date().isoformat():
        raise ValueError("MISSING_CONFLICTING_OR_FUTURE_PUBLICATION_DATE")
    date = next(iter(dates))
    if source == "federal_reserve_board" and re.search(r"monetary(\d{8})", url)[1] != date.replace("-", ""):
        raise ValueError("ARTICLE_URL_DATE_CONFLICT")
    title = next((clean(n.text()) for n in owned if n.tag in {"h1", "h2", "h3"} and clean(n.text())), "")
    if not title:
        title = next((clean(n.text()) for n in nodes if n.tag == "title"), "")
    offsets, pos = [], 0
    for i, paragraph in enumerate(paragraphs):
        offsets.append({"id": f"p{i+1:04d}", "start": pos, "end": pos + len(paragraph), "sha256": hashlib.sha256(paragraph.encode()).hexdigest(), "selector": f"official-narrative/p[{i+1}]"})
        pos += len(paragraph) + 2
    return {"title": title[:500], "text": joined, "paragraphs": offsets, "published_date": date, "published_at": None,
            "time_precision": "date", "publication_time_verified": False, "response_sha256": hashlib.sha256(raw).hexdigest(),
            "text_sha256": hashlib.sha256(joined.encode()).hexdigest(), "html_retained": False}


def feed_links(raw, source, retrieved_at, settings):
    if len(raw) > settings["maximum_response_bytes"] or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper() or b"\x00" in raw:
        raise ValueError("UNSAFE_OFFICIAL_FEED")
    tree = ET.fromstring(raw)
    values = []
    for node in tree.findall("./channel/item")[:50]:
        try:
            url = approved_url((node.findtext("link") or "").strip(), source)
            date = parsedate_to_datetime(node.findtext("pubDate"))
            if date.tzinfo is None:
                continue
            age = (as_utc(retrieved_at) - date).total_seconds()
            if 0 <= age <= settings["lookback_days"] * 86400 and url not in {x["url"] for x in values}:
                values.append({"url": url, "provider_published_at": date.isoformat()})
        except (ValueError, TypeError, OverflowError):
            continue
    return values[:settings["maximum_documents_per_feed"]]
