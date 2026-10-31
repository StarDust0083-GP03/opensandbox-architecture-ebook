"""Package rendered chapters as EPUB 3 with the standard library only."""
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]
XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
ET.register_namespace("", XHTML)
ET.register_namespace("epub", EPUB)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class Fragment(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element(f"{{{XHTML}}}div")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = ET.SubElement(self.stack[-1], f"{{{XHTML}}}{tag}", {k: v or k for k, v in attrs})
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        expected = f"{{{XHTML}}}{tag}"
        if len(self.stack) > 1 and self.stack[-1].tag == expected:
            self.stack.pop()
        elif tag not in VOID:
            raise ValueError(f"unbalanced HTML: {tag}")

    def handle_data(self, data):
        node = self.stack[-1]
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def xhtml(title, fragment):
    parser = Fragment()
    parser.feed(fragment)
    if len(parser.stack) != 1:
        raise ValueError("unclosed HTML element")
    page = ET.Element(f"{{{XHTML}}}html", {"lang": "zh-CN", "{http://www.w3.org/XML/1998/namespace}lang": "zh-CN"})
    head = ET.SubElement(page, f"{{{XHTML}}}head")
    ET.SubElement(head, f"{{{XHTML}}}title").text = title
    ET.SubElement(head, f"{{{XHTML}}}meta", {"charset": "utf-8"})
    ET.SubElement(head, f"{{{XHTML}}}link", {"rel": "stylesheet", "href": "book.css", "type": "text/css"})
    body = ET.SubElement(page, f"{{{XHTML}}}body")
    body.append(parser.root)
    return ET.tostring(page, encoding="utf-8", xml_declaration=True)


def main():
    payload = json.loads((ROOT / "wiki/epub-input.json").read_text())
    chapters = payload["chapters"]
    slugs = {c["slug"] for c in chapters}

    def links(match):
        base, sep, fragment = match[1].partition("#")
        if base.endswith(".html") and base.removesuffix(".html") in slugs:
            return 'href="' + base[:-5] + '.xhtml' + (sep + fragment if sep else '') + '"'
        return match[0]

    toc = ''.join(f'<li><a href="{c["slug"]}.xhtml">{escape(c["title"])}</a></li>' for c in chapters)
    nav = xhtml("全书目录", f'<nav epub:type="toc" id="toc"><h1>全书目录</h1><ol>{toc}</ol></nav>')
    # HTMLParser preserves colon attributes; serialize the EPUB namespace explicitly.
    nav = nav.replace(b'<html xmlns="', b'<html xmlns:epub="http://www.idpf.org/2007/ops" xmlns="', 1)
    uid = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "opensandbox-architecture-book-c2f55b20e945"))
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ''.join(f'<item id="c{i}" href="{c["slug"]}.xhtml" media-type="application/xhtml+xml"/>' for i, c in enumerate(chapters))
    spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="book-id">{uid}</dc:identifier>
<dc:title>{escape(payload['title'])}：{escape(payload['subtitle'])}</dc:title>
<dc:language>zh-CN</dc:language><dc:description>基于 opensandbox-group/OpenSandbox 快照 c2f55b20e945 的源码与官方文档独立编写的中文架构书稿。</dc:description>
<meta property="dcterms:modified">{modified}</meta></metadata>
<manifest><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/><item id="css" href="book.css" media-type="text/css"/>{manifest}</manifest>
<spine>{spine}</spine></package>'''
    css = 'body{font-family:serif;line-height:1.75;margin:1em}h1{font-size:1.65em}h2{font-size:1.3em;margin-top:1.8em}h3{font-size:1.1em}pre{font-size:.8em;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #ccc;padding:.7em}code{font-family:monospace}table{border-collapse:collapse;font-size:.85em}td,th{border-bottom:1px solid #ccc;padding:.4em;text-align:left}a{color:#29564c}blockquote{margin-left:1em;border-left:2px solid #ccc;padding-left:1em}'
    container = '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    (ROOT / "exports").mkdir(exist_ok=True)
    path = ROOT / "exports/opensandbox-architecture.epub"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/book.css", css)
        z.writestr("OEBPS/nav.xhtml", nav)
        for c in chapters:
            html = re.sub(r'href="([^"]+)"', links, c["html"])
            z.writestr(f'OEBPS/{c["slug"]}.xhtml', xhtml(c["title"], html))
    print(f"EPUB: {path.name}, {len(chapters)} chapters")


if __name__ == "__main__":
    main()
