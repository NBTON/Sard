"""Cultural DOCX Document Generator for Sard.

Produces standards-compliant Microsoft Word (.docx) documents with Arabic right-to-left
formatting (<w:bidi/>, <w:rtl/>), Sard cultural design tokens (Ink, Clay, Date, Olive, Gold, Card),
structured tables, styled callout boxes, and verified isnād citations.
"""

from __future__ import annotations

import io
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape


@dataclass
class DocxSection:
    title: str
    content: str
    bullets: List[str] = field(default_factory=list)
    badge: str = ""


@dataclass
class CulturalDocxDocument:
    title: str
    topic: str
    summary: str = ""
    region: str = "المملكة العربية السعودية"
    author: str = "سرد — المستشار الثقافي المعتمد"
    paragraphs: List[str] = field(default_factory=list)
    sections: List[DocxSection] = field(default_factory=list)
    key_takeaways: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    doc_id: str = field(default_factory=lambda: f"doc-{uuid.uuid4().hex[:8]}")


class DocxGenerator:
    """Generates standard OOXML .docx files with Arabic RTL support and Sard branding."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_docx(self, doc: CulturalDocxDocument) -> bytes:
        """Constructs a valid OOXML ZIP package (.docx) in memory and returns bytes."""
        stream = io.BytesIO()

        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. [Content_Types].xml
            zf.writestr("[Content_Types].xml", self._content_types_xml())

            # 2. _rels/.rels
            zf.writestr("_rels/.rels", self._global_rels_xml())

            # 3. docProps/app.xml & docProps/core.xml
            zf.writestr("docProps/app.xml", self._app_props_xml())
            zf.writestr("docProps/core.xml", self._core_props_xml(doc.title, doc.author))

            # 4. word/_rels/document.xml.rels
            zf.writestr("word/_rels/document.xml.rels", self._document_rels_xml())

            # 5. word/styles.xml
            zf.writestr("word/styles.xml", self._styles_xml())

            # 6. word/fontTable.xml
            zf.writestr("word/fontTable.xml", self._font_table_xml())

            # 7. word/document.xml
            zf.writestr("word/document.xml", self._document_xml(doc))

        return stream.getvalue()

    def _content_types_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '  <Default Extension="xml" ContentType="application/xml"/>\n'
            '  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>\n'
            '  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>\n'
            '  <Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>\n'
            '  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>\n'
            '  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>\n'
            "</Types>"
        )

    def _global_rels_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>\n'
            '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>\n'
            '  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>\n'
            "</Relationships>"
        )

    def _document_rels_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>\n'
            '  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>\n'
            "</Relationships>"
        )

    def _app_props_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">\n'
            "  <Application>سرد (Sard Cultural Agent)</Application>\n"
            "  <Company>وزارة الثقافة بالمملكة العربية السعودية</Company>\n"
            "</Properties>"
        )

    def _core_props_xml(self, title: str, author: str) -> str:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">\n'
            f"  <dc:title>{xml_escape(title)}</dc:title>\n"
            f"  <dc:creator>{xml_escape(author)}</dc:creator>\n"
            f"  <cp:lastModifiedBy>{xml_escape(author)}</cp:lastModifiedBy>\n"
            f'  <dcterms:created xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{now_iso}</dcterms:created>\n'
            f'  <dcterms:modified xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">{now_iso}</dcterms:modified>\n'
            "</cp:coreProperties>"
        )

    def _font_table_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:fonts xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            '  <w:font w:name="Noto Naskh Arabic">\n'
            '    <w:family w:val="auto"/>\n'
            '    <w:pitch w:val="variable"/>\n'
            "  </w:font>\n"
            '  <w:font w:name="IBM Plex Sans Arabic">\n'
            '    <w:family w:val="auto"/>\n'
            '    <w:pitch w:val="variable"/>\n'
            "  </w:font>\n"
            '  <w:font w:name="Arial">\n'
            '    <w:family w:val="swiss"/>\n'
            '    <w:pitch w:val="variable"/>\n'
            "  </w:font>\n"
            "</w:fonts>"
        )

    def _styles_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">\n'
            '  <w:docDefaults>\n'
            "    <w:rPrDefault>\n"
            "      <w:rPr>\n"
            '        <w:rFonts w:ascii="IBM Plex Sans Arabic" w:hAnsi="IBM Plex Sans Arabic" w:cs="Noto Naskh Arabic"/>\n'
            '        <w:sz w:val="24"/>\n'
            '        <w:szCs w:val="24"/>\n'
            '        <w:color w:val="141210"/>\n'
            "        <w:rtl/>\n"
            "      </w:rPr>\n"
            "    </w:rPrDefault>\n"
            "    <w:pPrDefault>\n"
            "      <w:pPr>\n"
            '        <w:jc w:val="right"/>\n'
            "        <w:bidi/>\n"
            '        <w:spacing w:line="360" w:lineRule="auto" w:after="160"/>\n'
            "      </w:pPr>\n"
            "    </w:pPrDefault>\n"
            "  </w:docDefaults>\n"
            "</w:styles>"
        )

    def _document_xml(self, doc: CulturalDocxDocument) -> str:
        body_parts: List[str] = []

        # 1. Ministry Brand Header Subtitle
        body_parts.append(
            '<w:p>'
            '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="80"/></w:pPr>'
            '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="18"/><w:szCs w:val="18"/><w:color w:val="BE4A24"/></w:rPr>'
            f'<w:t>المملكة العربية السعودية • وزارة الثقافة (سرد 2026)</w:t>'
            '</w:r>'
            '</w:p>'
        )

        # 2. Document Title
        body_parts.append(
            '<w:p>'
            '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:before="120" w:after="120"/></w:pPr>'
            '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="42"/><w:szCs w:val="42"/><w:color w:val="141210"/><w:rFonts w:cs="Noto Naskh Arabic"/></w:rPr>'
            f'<w:t>{xml_escape(doc.title)}</w:t>'
            '</w:r>'
            '</w:p>'
        )

        # 3. Meta info line
        body_parts.append(
            '<w:p>'
            '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="240"/><w:pBdr><w:bottom w:val="single" w:sz="12" w:space="8" w:color="C4A46A"/></w:pBdr></w:pPr>'
            '<w:r><w:rPr><w:rtl/><w:sz w:val="20"/><w:szCs w:val="20"/><w:color w:val="8A8178"/></w:rPr>'
            f'<w:t>المنطقة: {xml_escape(doc.region)} | التوثيق والمعتمد: {xml_escape(doc.author)}</w:t>'
            '</w:r>'
            '</w:p>'
        )

        # 4. Summary Box if provided
        summary_text = doc.summary or (doc.paragraphs[0] if doc.paragraphs else "")
        if summary_text:
            body_parts.append(
                '<w:tbl>'
                '<w:tblPr>'
                '<w:tblW w:w="5000" w:type="pct"/>'
                '<w:jc w:val="center"/>'
                '<w:tblBorders>'
                '<w:top w:val="single" w:sz="12" w:color="C4A46A"/>'
                '<w:left w:val="single" w:sz="12" w:color="C4A46A"/>'
                '<w:bottom w:val="single" w:sz="12" w:color="C4A46A"/>'
                '<w:right w:val="single" w:sz="12" w:color="C4A46A"/>'
                '</w:tblBorders>'
                '<w:shd w:val="clear" w:color="auto" w:fill="FAF7F1"/>'
                '<w:tblCellMar><w:top w:w="180" w:type="dxa"/><w:bottom w:w="180" w:type="dxa"/><w:left w:w="220" w:type="dxa"/><w:right w:w="220" w:type="dxa"/></w:tblCellMar>'
                '</w:tblPr>'
                '<w:tr>'
                '<w:tc>'
                '<w:p><w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="80"/></w:pPr>'
                '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="BE4A24"/></w:rPr>'
                '<w:t>ملخص التقرير والأصالة الثقافية</w:t></w:r></w:p>'
                '<w:p><w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="40"/></w:pPr>'
                '<w:r><w:rPr><w:rtl/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="141210"/></w:rPr>'
                f'<w:t>{xml_escape(summary_text)}</w:t></w:r></w:p>'
                '</w:tc>'
                '</w:tr>'
                '</w:tbl>'
            )
            body_parts.append('<w:p><w:pPr><w:bidi/><w:spacing w:after="160"/></w:pPr></w:p>')

        # 5. Main paragraphs
        start_p = 1 if (not doc.summary and len(doc.paragraphs) > 1) else 0
        for p in doc.paragraphs[start_p:]:
            if p.strip():
                body_parts.append(
                    '<w:p>'
                    '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="140"/></w:pPr>'
                    '<w:r><w:rPr><w:rtl/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="141210"/></w:rPr>'
                    f'<w:t>{xml_escape(p.strip())}</w:t>'
                    '</w:r>'
                    '</w:p>'
                )

        # 6. Structured Sections
        for sec in doc.sections:
            badge_suffix = f" ({sec.badge})" if sec.badge else ""
            body_parts.append(
                '<w:p>'
                '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:before="240" w:after="100"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="D4CBBD"/></w:pBdr></w:pPr>'
                '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/><w:color w:val="6E1F1F"/><w:rFonts w:cs="Noto Naskh Arabic"/></w:rPr>'
                f'<w:t>◆ {xml_escape(sec.title)}{xml_escape(badge_suffix)}</w:t>'
                '</w:r>'
                '</w:p>'
            )
            if sec.content:
                body_parts.append(
                    '<w:p>'
                    '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="120"/></w:pPr>'
                    '<w:r><w:rPr><w:rtl/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="141210"/></w:rPr>'
                    f'<w:t>{xml_escape(sec.content)}</w:t>'
                    '</w:r>'
                    '</w:p>'
                )
            for b in sec.bullets:
                body_parts.append(
                    '<w:p>'
                    '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="80"/><w:ind w:right="360"/></w:pPr>'
                    '<w:r><w:rPr><w:rtl/><w:b/><w:color w:val="BE4A24"/></w:rPr><w:t>• </w:t></w:r>'
                    '<w:r><w:rPr><w:rtl/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="141210"/></w:rPr>'
                    f'<w:t>{xml_escape(b)}</w:t>'
                    '</w:r>'
                    '</w:p>'
                )

        # 7. Key Takeaways Box
        if doc.key_takeaways:
            body_parts.append(
                '<w:p>'
                '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:before="240" w:after="100"/></w:pPr>'
                '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/><w:color w:val="4A513C"/></w:rPr>'
                '<w:t>الخلاصات المعرفية والأصالة التراثية:</w:t>'
                '</w:r>'
                '</w:p>'
            )
            for kt in doc.key_takeaways:
                body_parts.append(
                    '<w:p>'
                    '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="80"/><w:ind w:right="360"/></w:pPr>'
                    '<w:r><w:rPr><w:rtl/><w:b/><w:color w:val="4A513C"/></w:rPr><w:t>✔ </w:t></w:r>'
                    '<w:r><w:rPr><w:rtl/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="141210"/></w:rPr>'
                    f'<w:t>{xml_escape(kt)}</w:t>'
                    '</w:r>'
                    '</w:p>'
                )

        # 8. Sources & References
        if doc.sources:
            body_parts.append(
                '<w:p>'
                '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:before="280" w:after="100"/><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="4" w:color="D4CBBD"/></w:pBdr></w:pPr>'
                '<w:r><w:rPr><w:rtl/><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/><w:color w:val="BE4A24"/></w:rPr>'
                '<w:t>المراجع والتوثيق المعتمد:</w:t>'
                '</w:r>'
                '</w:p>'
            )
            for s in doc.sources:
                s_title = s.get("title") or s.get("source_name") or s.get("id", "")
                s_url = s.get("url") or s.get("source_url") or ""
                ref_text = f"{s_title} ({s_url})" if s_url else s_title
                body_parts.append(
                    '<w:p>'
                    '<w:pPr><w:jc w:val="right"/><w:bidi/><w:spacing w:after="60"/><w:ind w:right="280"/></w:pPr>'
                    '<w:r><w:rPr><w:rtl/><w:color w:val="8A8178"/></w:rPr><w:t>[مرجع] </w:t></w:r>'
                    '<w:r><w:rPr><w:rtl/><w:sz w:val="18"/><w:szCs w:val="18"/><w:color w:val="8A8178"/></w:rPr>'
                    f'<w:t>{xml_escape(ref_text)}</w:t>'
                    '</w:r>'
                    '</w:p>'
                )

        # Page setup
        body_parts.append(
            '<w:sectPr>'
            '<w:pgSz w:w="11906" w:h="16838"/>'  # A4 size in twips
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
            '<w:bidi/>'
            '</w:sectPr>'
        )

        inner = "\n".join(body_parts)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
            f"<w:body>\n{inner}\n</w:body>\n"
            "</w:document>"
        )


def render_cultural_docx_report(
    title: str,
    topic: str,
    content_paragraphs: Optional[List[str]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    key_takeaways: Optional[List[str]] = None,
    sources: Optional[List[Dict[str, str]]] = None,
    region: str = "المملكة العربية السعودية",
    summary: str = "",
    output_path: Optional[Path] = None,
) -> bytes:
    """Builds an Arabic RTL cultural Word (.docx) document and returns bytes."""
    sec_objs: List[DocxSection] = []
    if sections:
        for s in sections:
            sec_objs.append(
                DocxSection(
                    title=s.get("title", ""),
                    content=s.get("content", ""),
                    bullets=s.get("bullets", []),
                    badge=s.get("badge", ""),
                )
            )

    doc = CulturalDocxDocument(
        title=title or f"تقرير ثقافي: {topic}",
        topic=topic,
        summary=summary,
        region=region,
        paragraphs=content_paragraphs or [],
        sections=sec_objs,
        key_takeaways=key_takeaways or [],
        sources=sources or [],
    )

    gen = DocxGenerator()
    data = gen.build_docx(doc)

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    return data
