"""Check generated HTML links, anchors, source inventory and EPUB packaging."""
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links = [], []
        self.script_sources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        for attr in ("href", "src"):
            if attr in attrs:
                self.links.append(attrs[attr])
        if tag == "script" and "src" in attrs:
            self.script_sources.append(attrs["src"])


def main():
    entries = json.loads((ROOT / "wiki/epub-input.json").read_text())["chapters"]
    expected = len(entries)

    files = [ROOT / "wiki/index.html", ROOT / "wiki/search.html", ROOT / "wiki/book.html"]
    files.extend(sorted((ROOT / "wiki/chapters").glob("*.html")))
    pages = {}
    for path in files:
        page = Page()
        page.feed(path.read_text())
        assert len(page.ids) == len(set(page.ids)), f"duplicate ID in {path.name}"
        pages[path.resolve()] = page
    for path, page in pages.items():
        for value in page.links:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            assert target.exists(), f"missing target {value} from {path.name}"
            if parsed.fragment and target in pages and parsed.fragment != "resume":
                assert unquote(parsed.fragment) in pages[target].ids, f"missing anchor {value} from {path.name}"

    offline = pages[(ROOT / "wiki/book.html").resolve()]
    assert not offline.script_sources, "single-file HTML has external scripts"

    for entry in entries:
        md = (ROOT / 'book/chapters' / (entry['slug'] + '.md')).read_text()
        original_urls = set(re.findall(r'\]\((https?://[^)]+)\)', md))
        rendered = pages[(ROOT / 'wiki/chapters' / (entry['slug'] + '.html')).resolve()].links
        rendered_urls = {x for x in rendered if x.startswith('http')}
        assert original_urls <= rendered_urls, f"external source URL rewritten in {entry['slug']}"

    inventory = json.loads((ROOT / "sources/inventory.json").read_text())
    assert len(inventory["files"]) >= 40, "source inventory too small"
    for relative, meta in inventory["files"].items():
        assert meta["sha256"] and meta["url"].startswith("https://github.com/"), relative
        assert meta["lines"] > 0, relative

    with ZipFile(ROOT / "exports/opensandbox-architecture.epub") as z:
        assert z.infolist()[0].filename == "mimetype"
        assert z.infolist()[0].compress_type == ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        xml_files = {name: ET.fromstring(z.read(name)) for name in z.namelist()
                     if name.endswith((".xhtml", ".opf", ".xml"))}
        names = set(z.namelist())
        ids = {name: {e.attrib["id"] for e in tree.iter() if "id" in e.attrib}
               for name, tree in xml_files.items()}
        for name, tree in xml_files.items():
            for element in tree.iter():
                for attr in ("href", "src", "full-path"):
                    value = element.attrib.get(attr)
                    if not value:
                        continue
                    link = urlsplit(value)
                    if link.scheme or link.netloc:
                        continue
                    base = PurePosixPath(name).parent if attr != "full-path" else PurePosixPath()
                    target = str(base / unquote(link.path)) if link.path else name
                    assert target in names, f"EPUB missing resource {value} from {name}"
                    if link.fragment:
                        assert unquote(link.fragment) in ids.get(target, set()), f"EPUB missing anchor {value}"
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        opf = xml_files["OEBPS/content.opf"]
        manifest = {n.attrib["id"] for n in opf.findall("opf:manifest/opf:item", ns)}
        spine = opf.findall("opf:spine/opf:itemref", ns)
        assert len(spine) == expected
        assert all(n.attrib["idref"] in manifest for n in spine)
        assert any(n.attrib.get("properties") == "nav" for n in opf.findall("opf:manifest/opf:item", ns))

    prose = '\n'.join(p.read_text() for p in (ROOT / 'book/chapters').glob('*.md') if p.stem != 'appendix-code')
    assert not re.search(r"TODO|TBD|Lorem ipsum", prose), "placeholder in prose"
    print(f"PASS: {len(files)} HTML pages, internal targets/anchors, {len(inventory['files'])} source files, {expected} EPUB spine chapters and XML resources")
    print("External URLs are source references; their live availability is not checked here.")
    print("This is a structural check, not epubcheck certification or a runtime test of OpenSandbox.")


if __name__ == "__main__":
    main()
