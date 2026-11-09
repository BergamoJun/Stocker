"""Offline inline-XBRL extraction. Numeric evidence is never semantic approval."""

from decimal import Decimal, InvalidOperation, localcontext
from collections import Counter
from html.parser import HTMLParser
import re

from .sec_facts import cik
from .sec_refresh import canonical_hash
from .sec_transport import endpoint
from .validation import digest


def number(text, attributes):
    if attributes.get("continuedat") or attributes.get("xsi:nil", "").lower() in {"true", "1"}:
        raise ValueError("UNSUPPORTED_CONTINUED_OR_NIL_FACT")
    fmt = attributes.get("format", "").split(":")[-1].lower()
    if fmt not in {"", "numdotdecimal", "num-dot-decimal", "zerodash", "zero-dash", "fixed-zero"}:
        raise ValueError("UNSUPPORTED_IX_NUMBER_FORMAT")
    value = "".join(text.split()).replace("\u2212", "-")
    if fmt in {"zerodash", "zero-dash", "fixed-zero"}:
        if value not in {"-", "—", "–", "0", ""}: raise ValueError("INVALID_IX_ZERO")
        value = "0"
    if not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ValueError("INVALID_IX_NUMBER")
    if not re.fullmatch(r"-?\d{1,2}", attributes.get("scale", "0")):
        raise ValueError("UNSUPPORTED_IX_SCALE")
    scale = int(attributes.get("scale", "0"))
    if not -12 <= scale <= 12 or attributes.get("sign", "") not in {"", "-"} or (value.startswith("-") and attributes.get("sign")):
        raise ValueError("INVALID_IX_SIGN_OR_SCALE")
    if sum(c.isdigit() for c in value) > 60: raise ValueError("IX_NUMBER_TOO_LONG")
    with localcontext() as context:
        context.prec = 100
        amount = Decimal(value.replace(",", "")) * Decimal(10) ** scale
        if attributes.get("sign") == "-": amount = -amount
    if not amount.is_finite(): raise ValueError("NONFINITE_IX_VALUE")
    return format(amount, "f")


class FilingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts, self.units, self.facts, self.rows = {}, {}, [], {}
        self.context = self.unit = None
        self.active, self.fact_number = [], 0
        self.dom_ids = Counter()
        self.stack, self.row_stack, self.cell_stack = [], [], []
        self.row_number = self.cell_number = self.hidden = self.exclude = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"): self.dom_ids[attrs["id"]] += 1
        self.stack.append(tag)
        if tag == "ix:hidden": self.hidden += 1
        if tag == "ix:exclude": self.exclude += 1
        if tag == "tr":
            self.row_number += 1
            self.row_stack.append(self.row_number)
            self.rows[self.row_number] = []
        if tag in {"td", "th"}:
            self.cell_number += 1
            self.cell_stack.append(self.cell_number)
        if tag in {"xbrli:context", "context"}:
            self.context = {"id": attrs.get("id"), "dimensions": [], "parts": {}, "text": [], "start_tag": tag}
        elif self.context is not None:
            self.context["text"] = []
            if tag.split(":")[-1] == "identifier":
                self.context["parts"]["identifier_scheme"] = attrs.get("scheme")
            if tag.split(":")[-1] in {"explicitmember", "typedmember"}:
                self.context["dimensions"].append(attrs.get("dimension", "unrecognized_dimension"))
        if tag in {"xbrli:unit", "unit"}:
            self.unit = {"id": attrs.get("id"), "measures": [], "divided": False, "text": [], "start_tag": tag}
        elif self.unit is not None:
            self.unit["text"] = []
            if tag.split(":")[-1] == "divide": self.unit["divided"] = True
        if tag == "ix:nonfraction":
            self.fact_number += 1
            if self.fact_number > 100000: raise ValueError("TOO_MANY_IX_FACTS")
            self.active.append({"attributes": attrs, "text": [], "hidden": self.hidden > 0,
                "row_number": self.row_stack[-1] if self.row_stack else None, "ordinal": self.fact_number})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.context is not None: self.context["text"].append(data)
        if self.unit is not None: self.unit["text"].append(data)
        if not self.exclude:
            for item in self.active: item["text"].append(data)
        if self.row_stack and not self.hidden and not any(t in {"script", "style"} for t in self.stack[-3:]):
            self.rows[self.row_stack[-1]].append(data)

    def handle_endtag(self, tag):
        local = tag.split(":")[-1]
        if self.context is not None:
            if local in {"identifier", "startdate", "enddate", "instant"}:
                self.context["parts"][local] = "".join(self.context["text"]).strip()
            if tag == self.context["start_tag"]:
                key = self.context["id"]
                if not key or key in self.contexts: raise ValueError("DUPLICATE_OR_MISSING_XBRL_CONTEXT")
                self.contexts[key] = self.context
                self.context = None
        if self.unit is not None:
            if local == "measure": self.unit["measures"].append("".join(self.unit["text"]).strip())
            if tag == self.unit["start_tag"]:
                key = self.unit["id"]
                if not key or key in self.units: raise ValueError("DUPLICATE_OR_MISSING_XBRL_UNIT")
                self.units[key] = self.unit
                self.unit = None
        if tag == "ix:nonfraction" and self.active:
            self.facts.append(self.active.pop())
        if tag == "ix:hidden": self.hidden = max(0, self.hidden - 1)
        if tag == "ix:exclude": self.exclude = max(0, self.exclude - 1)
        if tag == "tr" and self.row_stack: self.row_stack.pop()
        if tag in {"td", "th"} and self.cell_stack: self.cell_stack.pop()
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]


def parse(raw, source_sha256, issuer, *, maximum_facts=100000):
    if len(raw) > 30000000 or b"<!ENTITY" in raw.upper():
        raise ValueError("UNSAFE_OR_OVERSIZED_SEC_DOCUMENT")
    parser = FilingParser()
    parser.feed(raw.decode("utf-8-sig"))
    parser.close()
    if parser.active or parser.context or parser.unit: raise ValueError("UNTERMINATED_XBRL_EVIDENCE")
    if len(parser.facts) > maximum_facts: raise ValueError("TOO_MANY_IX_FACTS")
    facts = []
    duplicate_ids = {key for key, count in parser.dom_ids.items() if count > 1}
    for item in sorted(parser.facts, key=lambda r: r["ordinal"]):
        attrs = item["attributes"]
        context = parser.contexts.get(attrs.get("contextref"), {})
        parts = context.get("parts", {})
        try: same_entity = cik(parts.get("identifier", "")) == cik(issuer) and parts.get("identifier_scheme") in {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
        except ValueError: same_entity = False
        unit = parser.units.get(attrs.get("unitref"), {})
        measures = [v.lower().split(":")[-1] for v in unit.get("measures", [])]
        unit_name = "USD/shares" if unit.get("divided") and measures == ["usd", "shares"] else "USD" if not unit.get("divided") and measures == ["usd"] else "unsupported"
        try: amount, status = number("".join(item["text"]), attrs), "parsed_numeric_evidence_only"
        except (ValueError, InvalidOperation): amount, status = None, "unsupported_numeric_representation"
        dom_id = attrs.get("id")
        if dom_id in duplicate_ids: status = "duplicate_dom_id"
        result = {"fact_id": "ix:" + canonical_hash([source_sha256, item["ordinal"]]), "ordinal": item["ordinal"],
            "dom_id": dom_id, "tag": attrs.get("name"), "context_ref": attrs.get("contextref"),
            "value": amount, "unit": unit_name, "start": parts.get("startdate"), "end": parts.get("enddate") or parts.get("instant"),
            "is_instant": bool(parts.get("instant")), "same_entity": same_entity, "dimensions": context.get("dimensions", []),
            "hidden": item["hidden"], "status": status, "row_number": item["row_number"],
            "row_text": " ".join(" ".join(parser.rows.get(item["row_number"], [])).split())[:4000],
            "format": attrs.get("format"), "scale": attrs.get("scale", "0"), "sign": attrs.get("sign", ""),
            "display_text": "".join(item["text"]).strip(), "source_sha256": source_sha256}
        facts.append(result)
    return facts


def document(source, issuer):
    if source["kind"] != "document" or endpoint(source["source_url"]) != "document":
        raise ValueError("SEC_FILING_DOCUMENT_REQUIRED")
    from pathlib import Path
    path = Path(source["raw_path"])
    if not path.is_file() or digest(path) != source["raw_sha256"]:
        raise ValueError("SEC_FILING_BYTES_CHANGED")
    return parse(path.read_bytes(), source["raw_sha256"], issuer)
