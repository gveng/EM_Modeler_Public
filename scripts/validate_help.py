"""Validate local links and screenshot assets referenced by the HTML help."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "docs" / "HELP.html"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class HelpParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.references: list[tuple[str, str]] = []
        self.language = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.language = values.get("lang", "") or ""
        element_id = values.get("id")
        if element_id:
            self.ids.add(element_id)
        for attribute in (("img", "src"), ("a", "href")):
            if tag == attribute[0] and values.get(attribute[1]):
                self.references.append((attribute[1], values[attribute[1]] or ""))


def validate() -> list[str]:
    parser = HelpParser()
    parser.feed(HELP.read_text(encoding="utf-8"))
    errors: list[str] = []
    if parser.language != "en":
        errors.append(f"Expected <html lang=\"en\">, got {parser.language!r}")

    for kind, reference in parser.references:
        parsed = urlparse(reference)
        if parsed.scheme or parsed.netloc:
            continue
        target = (HELP.parent / unquote(parsed.path)).resolve() if parsed.path else HELP
        if not target.is_file():
            errors.append(f"Missing local {kind}: {reference}")
            continue
        if parsed.fragment and target == HELP and unquote(parsed.fragment) not in parser.ids:
            errors.append(f"Missing in-page anchor: #{unquote(parsed.fragment)}")
        if kind == "src":
            if target.suffix.lower() != ".png":
                errors.append(f"Help screenshot is not a PNG capture: {reference}")
            elif target.read_bytes()[:8] != PNG_SIGNATURE:
                errors.append(f"Invalid PNG screenshot: {reference}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Help OK: {HELP.relative_to(ROOT)}; local links, anchors and screenshots are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
