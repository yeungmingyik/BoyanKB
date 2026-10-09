import argparse
import base64
import copy
import decimal
import hashlib
import html
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
import zipfile

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn as wqn
from docx.shared import Inches, Pt as WordPt, RGBColor as WordColor
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.worksheet.datavalidation import DataValidation, DataValidationList
from pptx import Presentation
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement as PresentationElement
from pptx.util import Pt
from pptx.dml.color import RGBColor

ROOT = pathlib.Path(os.environ.get("BOYAN_TEMPLATE_ROOT", "/app/branding/templates"))
MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown; charset=utf-8",
}
ACCENTS = {"teaching": "2D64B0", "business": "1F4884", "operations": "379CB7"}
FONT = "Microsoft YaHei"


class RenderError(Exception):
    pass


def fail(code):
    raise RenderError(code)


def string(value, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        fail("invalid_text")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|\{\{[^}]*\}\}", value):
        fail("invalid_text")
    return value


def validate(request):
    if not isinstance(request, dict) or not isinstance(request.get("format"), str) or not isinstance(request.get("scene"), str) or request["format"] not in MIME or request["scene"] not in ACCENTS:
        fail("invalid_format_or_scene")
    if set(request) - {"format", "scene", "brand_id", "template_version", "job_id", "tenant_id", "outline_hash", "document", "sources", "budget"}:
        fail("unknown_request_field")
    if request.get("brand_id") != "boyan-culture" or request.get("template_version") != "boyan-v1":
        fail("invalid_brand_version")
    try:
        uuid.UUID(request["job_id"])
    except (KeyError, ValueError, TypeError, AttributeError):
        fail("invalid_job")
    if type(request.get("tenant_id")) is not int or request["tenant_id"] <= 0:
        fail("invalid_tenant")
    if not isinstance(request.get("outline_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", request["outline_hash"]):
        fail("invalid_outline_hash")
    doc = request.get("document")
    if not isinstance(doc, dict):
        fail("invalid_document")
    string(doc.get("title"), 100, True)
    if request["format"] == "pptx" and len(doc["title"]) > 36:
        fail("presentation_title_too_long")
    sections = doc.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 40:
        fail("invalid_sections")
    for section in sections:
        if not isinstance(section, dict):
            fail("invalid_section")
        string(section.get("heading"), 100, True)
        if request["format"] == "pptx" and len(section["heading"]) > 40:
            fail("presentation_heading_too_long")
        string(section.get("body", ""), 12000)
        string(section.get("notes", ""), 5000)
        bullets = section.get("bullets", [])
        if not isinstance(bullets, list) or len(bullets) > 40:
            fail("invalid_bullets")
        for bullet in bullets:
            string(bullet, 2000, True)
        tables = section.get("tables", [])
        if not isinstance(tables, list) or len(tables) > 4:
            fail("invalid_tables")
        for table in tables:
            if not isinstance(table, dict):
                fail("invalid_table")
            headers, rows = table.get("headers"), table.get("rows")
            if not isinstance(headers, list) or not 1 <= len(headers) <= 8 or not isinstance(rows, list) or len(rows) > 150:
                fail("invalid_table_size")
            for cell in headers:
                string(cell, 100, True)
            for row in rows:
                if not isinstance(row, list) or len(row) != len(headers):
                    fail("invalid_table_row")
                for cell in row:
                    string(cell, 1000)
    sources = request.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 30:
        fail("missing_sources")
    for source in sources:
        if not isinstance(source, dict):
            fail("invalid_source")
        string(source.get("id", source.get("chunk_id", "")), 100, True)
        string(source.get("title"), 300, True)
        string(source.get("excerpt", ""), 24000)
    budget = request.get("budget")
    if budget is not None:
        if not isinstance(budget, dict) or not isinstance(budget.get("items"), list) or not 1 <= len(budget["items"]) <= 150:
            fail("invalid_budget")
        for item in budget["items"]:
            if not isinstance(item, dict):
                fail("invalid_budget_item")
            string(item.get("label"), 150, True)
            for field in ("quantity", "unit_price"):
                value = item.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value > 100000000:
                    fail("invalid_budget_number")
    template = ROOT / (request["scene"] + "." + request["format"])
    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        entry = next((v for v in manifest["templates"] if v["id"] == request["scene"] + "-" + request["format"]), None)
        valid = manifest.get("version") == "boyan-v1" and manifest.get("brand_id") == "boyan-culture" and entry and entry.get("file") == template.name and template.resolve().parent == ROOT.resolve() and hashlib.sha256(template.read_bytes()).hexdigest() == entry["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        fail("template_integrity_failed")
    return template


def paragraphs(value):
    return [line.strip() for line in value.splitlines() if line.strip()]


def branded_title(title):
    title = title.strip()
    return title if title.startswith("博研文化") else "博研文化 " + title


def source_lines(request):
    return [f"[{index}] {s['title']}　分块 {s.get('chunk_id', s.get('id'))}" for index, s in enumerate(request["sources"], 1)]


def docx_table(doc, headers, rows, accent):
    table = doc.add_table(rows=1, cols=len(headers))
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = str(value)
    repeat = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for ri, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            props = cell._tc.get_or_add_tcPr()
            shade = OxmlElement("w:shd")
            shade.set(wqn("w:fill"), accent if ri == 0 else "F0F5FB" if ri % 2 == 0 else "FFFFFF")
            props.append(shade)
            margins = OxmlElement("w:tcMar")
            borders = OxmlElement("w:tcBorders")
            for edge in ("top", "bottom", "left", "right"):
                margin = OxmlElement("w:" + edge)
                margin.set(wqn("w:w"), "100")
                margin.set(wqn("w:type"), "dxa")
                margins.append(margin)
                border = OxmlElement("w:" + edge)
                for key, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
                    border.set(wqn("w:" + key), value)
                borders.append(border)
            props.append(margins)
            props.append(borders)
            for p in cell.paragraphs:
                p.paragraph_format.space_after = WordPt(0)
                for run in p.runs:
                    run.font.name = FONT
                    run.font.size = WordPt(10)
                    run.font.bold = ri == 0
                    run.font.color.rgb = WordColor.from_string("FFFFFF" if ri == 0 else "000000")


def render_docx(request, template, destination):
    doc = Document(template)
    for element in list(doc.element.body):
        if element.tag != wqn("w:sectPr"):
            doc.element.body.remove(element)
    content = request["document"]
    doc.add_paragraph(content["title"], "Title")
    for section in content["sections"]:
        doc.add_heading(section["heading"], level=1)
        for p in paragraphs(section.get("body", "")):
            doc.add_paragraph(p)
        for bullet in section.get("bullets") or []:
            doc.add_paragraph(bullet, "List Bullet")
        for table in section.get("tables") or []:
            docx_table(doc, table["headers"], table["rows"], ACCENTS[request["scene"]])
        if section.get("notes"):
            doc.add_paragraph("讲师提示", "Heading 2")
            for note in paragraphs(section["notes"]):
                doc.add_paragraph(note)
    if request.get("budget"):
        doc.add_heading("预算测算", level=1)
        rows = [[v["label"], str(v["quantity"]), f"{v['unit_price']:.2f}", str(line_amount(v))] for v in request["budget"]["items"]]
        docx_table(doc, ["项目", "数量", "单价（元）", "金额（元）"], rows, ACCENTS[request["scene"]])
        doc.add_paragraph("预算合计：" + str(sum(line_amount(v) for v in request["budget"]["items"])) + " 元。预算假设须核实，不能作为总部正式报价。")
    doc.add_heading("资料来源", level=1)
    for source in source_lines(request):
        doc.add_paragraph(source)
    doc.core_properties.title = content["title"]
    doc.core_properties.author = "博研文化"
    doc.core_properties.comments = ""
    doc.save(destination)
    Document(destination)


def set_text(shape, value):
    frame = shape.text_frame
    paragraph = frame.paragraphs[0]
    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
    formatting = copy.deepcopy(run._r.rPr)
    paragraph_props = copy.deepcopy(paragraph._p.pPr)
    frame.clear()
    for index, line in enumerate(value.splitlines() or [""]):
        p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        if paragraph_props is not None:
            if p._p.pPr is not None:
                p._p.remove(p._p.pPr)
            p._p.insert(0, copy.deepcopy(paragraph_props))
        r = p.add_run()
        r.text = line
        if formatting is not None:
            r._r.insert(0, copy.deepcopy(formatting))
        r.font.name = FONT
        ea = r._r.get_or_add_rPr().find("{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        if ea is None:
            ea = PresentationElement("a:ea")
            r._r.get_or_add_rPr().append(ea)
        ea.set("typeface", FONT)


def clone_slide(deck, source):
    slide = deck.slides.add_slide(source.slide_layout)
    for shape in list(slide.shapes):
        slide.shapes._spTree.remove(shape.element)
    mapping = {}
    for relationship in source.part.rels.values():
        if "notesSlide" in relationship.reltype or "slideLayout" in relationship.reltype:
            continue
        mapping[relationship.rId] = slide.part.relate_to(relationship.target_part, relationship.reltype)
    for shape in source.shapes:
        element = copy.deepcopy(shape.element)
        for node in element.iter():
            for attribute, value in list(node.attrib.items()):
                if attribute.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}") and value in mapping:
                    node.set(attribute, mapping[value])
        slide.shapes._spTree.insert_element_before(element, "p:extLst")
    background = source.element.cSld.find("{http://schemas.openxmlformats.org/presentationml/2006/main}bg")
    if background is not None:
        slide.element.cSld.insert(0, copy.deepcopy(background))
    return slide


def text_boundaries(text):
    blocked = set()
    for match in re.finditer(r"[A-Za-z0-9]+(?:[._:/@%+\-][A-Za-z0-9]+)*(?:分钟|小时|个月|天|周|月|年|元|人|次|%)?", text):
        blocked.update(range(match.start() + 1, match.end()))
    for match in re.finditer(r"\d+(?:[.,]\d+)*(?:分钟|小时|个月|天|周|月|年|元|人|次|%)?", text):
        blocked.update(range(match.start() + 1, match.end()))
    return [index for index in range(1, len(text) + 1) if index == len(text) or (
        index not in blocked and not unicodedata.combining(text[index])
        and text[index] not in "，。；：、！？,.!?;:%）】》”’"
        and text[index - 1] not in "（【《“‘"
    )]


def chunks(text, size=50):
    result = []
    while len(text) > size:
        count = math.ceil(len(text) / size)
        target = math.ceil(len(text) / count)
        minimum = max(1, size // 3)
        lower = max(minimum, len(text) - (count - 1) * size)
        upper = min(size, len(text) - minimum)
        boundaries = text_boundaries(text)
        choices = [index for index in boundaries if lower <= index <= upper]
        if not choices:
            choices = [index for index in boundaries if index <= size]
        if not choices:
            fail("presentation_token_too_long")

        def priority(index):
            prefix = text[:index].rstrip()
            boundary = 0 if re.search(r"[。！？.!?；;][）】》”’]*$", prefix) else 1 if re.search(r"[，：、,:][）】》”’]*$", prefix) else 2 if text[index - 1].isspace() else 3
            return boundary, abs(index - target), index

        index = min(choices, key=priority)
        result.append(text[:index])
        text = text[index:]
    return result + [text]


def point_pages(values):
    count = max(1, math.ceil(len(values) / 3))
    base, extra = divmod(len(values), count)
    offset = 0
    for index in range(count):
        size = base + (index < extra)
        yield offset, values[offset:offset + size]
        offset += size


def text_width(value):
    return sum(0 if unicodedata.combining(char) else 1 if unicodedata.east_asian_width(char) in "WF" else .62 for char in value)


def wrap_text(text, capacity):
    output = []
    for source in text.splitlines() or [""]:
        offset, line = 0, ""
        for boundary in text_boundaries(source):
            unit = source[offset:boundary]
            if line and text_width(line + unit) > capacity:
                output.append(line)
                line = ""
            line += unit
            offset = boundary
        output.append(line)
    return output


def fitted_text(shape, value, minimum=None):
    existing = next((run.font.size.pt for paragraph in shape.text_frame.paragraphs for run in paragraph.runs if run.font.size), 24)
    choices = [existing] + ([minimum] if minimum and minimum < existing else [])
    width = (shape.width - shape.text_frame.margin_left - shape.text_frame.margin_right) / 12700
    height = (shape.height - shape.text_frame.margin_top - shape.text_frame.margin_bottom) / 12700
    for size in choices:
        capacity = width / (size * 1.08)
        lines = wrap_text(value, capacity)
        candidates = [lines]
        if len(lines) > 1 and "\n" not in value and len(lines[-1]) < len(lines[0]) * .5:
            units = text_width(value)
            candidates.insert(0, wrap_text(value, min(capacity, math.ceil(units / len(lines)) + .5)))
        for lines in candidates:
            if all(text_width(line) <= capacity for line in lines) and len(lines) * size * (1.24 if shape.name == "heading" else 1.12) <= height:
                set_text(shape, "\n".join(lines))
                shape.text_frame.word_wrap = False
                for paragraph in shape.text_frame.paragraphs:
                    paragraph.line_spacing = 1.05
                    paragraph.space_before = Pt(0)
                    paragraph.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.font.size = Pt(size)
                return
    fail("presentation_text_does_not_fit")


def presentation_table_pages(deck, headers, rows):
    size = 18 if len(headers) <= 4 else 14
    width = deck.slide_width * .896 / 12700 / len(headers) - 12
    capacity = width / (size * 1.08)
    available = deck.slide_height * .52 / 12700

    def height(values):
        lines = [wrap_text(value, capacity) for value in values]
        if any(text_width(line) > capacity for group in lines for line in group):
            fail("presentation_table_cell_too_long")
        return max(len(group) for group in lines) * size * 1.15 + 12

    header_height = height(headers)
    if header_height > available * .4:
        fail("presentation_table_header_too_long")
    page, page_heights, used = [], [], header_height
    for row in rows:
        row_height = height(row)
        if row_height + header_height > available:
            fail("presentation_table_cell_too_long")
        if page and (used + row_height > available or len(page) >= 6):
            yield page, [header_height] + page_heights, size, capacity
            page, page_heights, used = [], [], header_height
        page.append(row)
        page_heights.append(row_height)
        used += row_height
    if page or not rows:
        yield page, [header_height] + page_heights, size, capacity


def render_pptx(request, template, destination):
    deck = Presentation(template)
    originals = list(deck.slides)
    created = []

    def create(index):
        if len(created) >= 80:
            fail("presentation_too_large")
        slide = clone_slide(deck, originals[index])
        created.append(slide)
        return slide

    def group_notes(slides, notes, group="本节", section_timing=False):
        sources = "\n".join(source_lines(request))
        for index, slide in enumerate(slides, 1):
            parts = []
            if len(slides) > 1:
                parts.append(f"{group}共 {len(slides)} 页，当前第 {index}/{len(slides)} 页。")
                if section_timing and notes:
                    parts.append("以下讲师备注中的预计时长为整节总时长，不逐页累加。")
            parts.extend(value for value in (notes, sources) if value)
            slide.notes_slide.notes_text_frame.text = "\n".join(parts)

    def table_pages(heading, headers, rows):
        slides = []
        for page_index, (page_rows, heights, size, capacity) in enumerate(presentation_table_pages(deck, headers, rows)):
            slide = create(4)
            slides.append(slide)
            for shape in list(slide.shapes):
                if shape.has_table:
                    shape.element.getparent().remove(shape.element)
            fitted_text(next(s for s in slide.shapes if s.name == "heading"), heading + ("（续）" if page_index else ""), 27)
            shape = slide.shapes.add_table(len(page_rows)+1, len(headers), int(deck.slide_width*.052), int(deck.slide_height*.31), int(deck.slide_width*.896), int(sum(heights)*12700))
            for row_index, values in enumerate([headers]+page_rows):
                shape.table.rows[row_index].height = int(heights[row_index]*12700)
                for column, value in enumerate(values):
                    cell = shape.table.cell(row_index, column)
                    cell.text = "\n".join(wrap_text(value, capacity))
                    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                    cell.margin_left = cell.margin_right = Pt(6)
                    cell.margin_top = cell.margin_bottom = Pt(4)
                    cell.text_frame.word_wrap = False
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor.from_string(ACCENTS[request["scene"]] if row_index == 0 else "F0F5FB" if row_index % 2 == 0 else "FFFFFF")
                    for paragraph in cell.text_frame.paragraphs:
                        paragraph.space_before = paragraph.space_after = Pt(0)
                        paragraph.line_spacing = 1.05
                        for run in paragraph.runs:
                            run.font.name = FONT
                            run.font.size = Pt(size)
                            run.font.bold = row_index == 0
                            run.font.color.rgb = RGBColor.from_string("FFFFFF" if row_index == 0 else "182A43")
        return slides

    cover = create(0)
    names = {s.name: s for s in cover.shapes}
    title = request["document"]["title"]
    if len(title) > 36:
        fail("presentation_title_too_long")
    fitted_text(names["cover-title"], title, 44)
    set_text(names["cover-subtitle"], {"teaching": "教学课件", "business": "合作与沟通", "operations": "活动与执行"}[request["scene"]])
    set_text(names["cover-context"], "")
    for section in request["document"]["sections"]:
        section_slides = []
        units = []
        for value in paragraphs(section.get("body", "")) + (section.get("bullets") or []):
            units.extend(chunks(value, 48))
        for start, page_units in point_pages(units):
            slide = create(1)
            section_slides.append(slide)
            named = {s.name: s for s in slide.shapes}
            fitted_text(named["heading"], section["heading"] + ("（续）" if start else ""), 27)
            for index in range(3):
                text = page_units[index] if index < len(page_units) else ""
                set_text(named["number-"+str(index)], str(start+index+1).zfill(2) if text else "")
                fitted_text(named["point-"+str(index)], text)
        for table in section.get("tables") or []:
            section_slides.extend(table_pages(section["heading"], table["headers"], table["rows"]))
        group_notes(section_slides, section.get("notes", ""), section_timing=True)
    if request.get("budget"):
        rows = [[item["label"], str(item["quantity"]), f"{item['unit_price']:.2f}", str(line_amount(item))] for item in request["budget"]["items"]]
        rows.append(["预算合计", "", "", str(sum(line_amount(item) for item in request["budget"]["items"]))])
        budget_slides = table_pages("预算测算", ["项目", "数量", "单价（元）", "金额（元）"], rows)
        group_notes(budget_slides, "预算假设须核实，不能作为总部正式报价。", group="预算测算")
    source_units = []
    for index, source in enumerate(request["sources"], 1):
        source_units.extend((index, value) for value in chunks(source["title"], 48))
    for _, page_units in point_pages(source_units):
        slide = create(1)
        named = {s.name:s for s in slide.shapes}
        set_text(named["heading"], "资料来源")
        for index in range(3):
            item = page_units[index] if index < len(page_units) else None
            set_text(named["number-"+str(index)], str(item[0]).zfill(2) if item else "")
            fitted_text(named["point-"+str(index)], item[1] if item else "")
        slide.notes_slide.notes_text_frame.text = "\n".join(source_lines(request))
    for index in range(len(originals)-1,-1,-1):
        item = deck.slides._sldIdLst[index]
        deck.part.drop_rel(item.rId)
        deck.slides._sldIdLst.remove(item)
    for page, slide in enumerate(deck.slides,1):
        for shape in slide.shapes:
            if shape.name == "page":
                set_text(shape,str(page).zfill(2))
    deck.core_properties.title = title
    deck.core_properties.author = "博研文化"
    deck.save(destination)
    Presentation(destination)


def line_amount(item):
    return (decimal.Decimal(str(item["quantity"])) * decimal.Decimal(str(item["unit_price"]))).quantize(decimal.Decimal(".01"), rounding=decimal.ROUND_HALF_UP)


def safe_cell(value):
    return "'"+value if isinstance(value,str) and value.startswith(("=", "+", "-", "@")) else value


def render_xlsx(request, template, destination):
    workbook = load_workbook(template)
    sheet = workbook.active
    sheet.data_validations = DataValidationList()
    sheet.conditional_formatting = ConditionalFormattingList()
    sheet.auto_filter.ref = None
    sheet.tables.clear()
    sheet.print_area = ""
    for key in list(sheet.row_dimensions):
        if key >= 4:
            del sheet.row_dimensions[key]
    for merged in list(sheet.merged_cells.ranges):
        sheet.unmerge_cells(str(merged))
    sheet.delete_rows(4, sheet.max_row)
    sheet["A2"] = branded_title(request["document"]["title"])
    sheet.merge_cells("A2:E2")
    sheet["A2"].font = Font(name=FONT,size=17,bold=True,color="1F4884")
    accent = ACCENTS[request["scene"]]
    budget = request.get("budget")
    row = 5
    for section in request["document"]["sections"]:
        sheet.cell(row,1,safe_cell(section["heading"])).font = Font(name=FONT,size=12,bold=True,color="000000")
        row += 1
        for value in paragraphs(section.get("body", "")) + (section.get("bullets") or []):
            sheet.cell(row,1,safe_cell(value))
            sheet.merge_cells(start_row=row,start_column=1,end_row=row,end_column=5)
            sheet.cell(row,1).alignment = Alignment(wrap_text=True,vertical="center")
            sheet.row_dimensions[row].height = max(28, 22*math.ceil(len(value)/65))
            row += 1
        for table in section.get("tables") or []:
            for ri, values in enumerate([table["headers"]]+table["rows"]):
                for column, value in enumerate(values,1):
                    cell=sheet.cell(row,column,safe_cell(value))
                    cell.font=Font(name=FONT,size=11,bold=ri==0,color="FFFFFF" if ri==0 else "182A43")
                    cell.fill=PatternFill("solid",fgColor=accent if ri==0 else "F0F5FB" if ri%2==0 else "FFFFFF")
                    cell.alignment=Alignment(wrap_text=True,vertical="center")
                sheet.row_dimensions[row].height=max(30,20*max(math.ceil(len(v)/22) for v in values))
                row += 1
            row += 1
        row += 1
    total_cell = None
    expected = None
    if budget:
        for column,value in enumerate(["费用项目","数量","单价（元）","金额（元）"],1):
            sheet.cell(row,column,value).fill=PatternFill("solid",fgColor=accent)
            sheet.cell(row,column).font=Font(name=FONT,color="FFFFFF",bold=True)
        row += 1
        first=row
        for item in budget["items"]:
            sheet.cell(row,1,safe_cell(item["label"]))
            sheet.cell(row,2,item["quantity"])
            sheet.cell(row,3,item["unit_price"])
            sheet.cell(row,4,f"=ROUND(B{row}*C{row},2)")
            for column in (2,3):
                sheet.cell(row,column).font=Font(name=FONT,color="0000FF",size=11)
                sheet.cell(row,column).fill=PatternFill("solid",fgColor="FFF8DF")
                sheet.cell(row,column).number_format='#,##0.00'
            sheet.cell(row,4).number_format='#,##0.00'
            row += 1
        numeric = DataValidation(type="decimal", operator="between", formula1=0, formula2=100000000, allow_blank=False)
        numeric.showErrorMessage = True
        numeric.errorTitle = "请输入有效预算数值"
        numeric.error = "数量和单价必须介于零和一亿元之间。"
        sheet.add_data_validation(numeric)
        numeric.add(f"B{first}:C{row-1}")
        sheet.cell(row,1,"预算合计（元）")
        sheet.cell(row,4,f"=SUM(D{first}:D{row-1})")
        total_cell=f"D{row}"
        expected=sum(line_amount(item) for item in budget["items"])
        row += 2
        sheet.cell(row,1,"预算为测算，报价与数量须按实际情况核实。")
        row += 2
    sheet.cell(row,1,"资料来源").font=Font(name=FONT,bold=True)
    for value in source_lines(request):
        row += 1
        sheet.cell(row,1,value)
        sheet.merge_cells(start_row=row,start_column=1,end_row=row,end_column=5)
        sheet.cell(row,1).alignment=Alignment(wrap_text=True,vertical="center")
        sheet.row_dimensions[row].height=38
    sheet.print_options.horizontalCentered=True
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    sheet.page_setup.orientation="landscape"
    sheet.page_setup.paperSize=sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth=1
    sheet.page_setup.fitToHeight=0
    sheet.freeze_panes="A5"
    sheet.print_title_rows="1:3"
    sheet.print_area=f"A1:{sheet.cell(1,max(5,sheet.max_column)).column_letter}{row}"
    workbook.properties.creator="博研文化"
    workbook.properties.title=request["document"]["title"]
    workbook.save(destination)
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        fail("spreadsheet_recalculation_unavailable")
    recalc = destination.parent / "recalculated"
    recalc.mkdir()
    profile = destination.parent / "lo-profile"
    subprocess.run([executable,"-env:UserInstallation="+profile.as_uri(),"--headless","--convert-to","xlsx","--outdir",str(recalc),str(destination)],check=True,timeout=45,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    recalculated = recalc / destination.name
    if not recalculated.is_file():
        fail("spreadsheet_recalculation_failed")
    values=load_workbook(recalculated,data_only=True)
    if total_cell:
        try:
            actual_total=decimal.Decimal(str(values.active[total_cell].value)).quantize(decimal.Decimal(".01"))
            amounts=[decimal.Decimal(str(values.active.cell(first+index,4).value)).quantize(decimal.Decimal(".01")) for index in range(len(budget["items"]))]
        except decimal.InvalidOperation:
            fail("spreadsheet_result_missing")
        if actual_total != expected or amounts != [line_amount(item) for item in budget["items"]]:
            fail("spreadsheet_total_mismatch")
    for tab in values:
        for cells in tab.iter_rows():
            for cell in cells:
                if cell.data_type=="e":
                    fail("spreadsheet_formula_error")
    shutil.copyfile(recalculated,destination)


def render_md(request, template, destination):
    doc=request["document"]
    lines=[]
    def literal(value):
        return re.sub(r"([\\`*\[\]])", r"\\\1", html.escape(value, quote=False))
    for section in doc["sections"]:
        lines += ["## "+literal(section["heading"]),"",literal(section.get("body", "")),""]
        lines += ["- "+literal(value) for value in section.get("bullets") or []]
        lines += [""]
        for table in section.get("tables") or []:
            lines += ["| "+" | ".join(literal(v).replace("|", "\\|").replace("\n", " ") for v in table["headers"])+" |", "| "+" | ".join("---" for _ in table["headers"])+" |"]
            lines += ["| "+" | ".join(literal(v).replace("|", "\\|").replace("\n", " ") for v in row)+" |" for row in table["rows"]]
            lines += [""]
    if request.get("budget"):
        lines += ["## 预算测算","","| 项目 | 数量 | 单价（元） | 金额（元） |","| --- | --- | --- | --- |"]
        for item in request["budget"]["items"]:
            label=literal(item["label"]).replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {label} | {item['quantity']} | {item['unit_price']:.2f} | {line_amount(item)} |")
        lines += ["", "预算合计："+str(sum(line_amount(v) for v in request["budget"]["items"]))+" 元。测算须核实，不能作为总部正式报价。", ""]
    title = branded_title(doc["title"])
    template_text = template.read_text(encoding="utf-8").replace("# 博研文化 {{title}}", "# {{title}}", 1)
    if request["scene"] == "business":
        template_text = template_text.replace("\n\n合作方案\n\n", "\n\n", 1)
    content=template_text.replace("{{title}}",literal(title)).replace("{{content}}","\n".join(lines)).replace("{{sources}}","\n".join(literal(source) for source in source_lines(request)))
    if re.search(r"\{\{[^}]*\}\}",content):
        fail("unresolved_template_field")
    destination.write_text(content,encoding="utf-8")
    if destination.read_text(encoding="utf-8") != content:
        fail("markdown_roundtrip_failed")


def generate(request):
    template=validate(request)
    kind=request["format"]
    filename="博研文化-"+request["job_id"][:8]+"."+kind
    with tempfile.TemporaryDirectory(prefix="boyan-render-") as temp:
        destination=pathlib.Path(temp)/filename
        {"docx":render_docx,"pptx":render_pptx,"xlsx":render_xlsx,"md":render_md}[kind](request,template,destination)
        data=destination.read_bytes()
        if len(data)>20*1024*1024:
            fail("artifact_too_large")
        if kind!="md":
            with zipfile.ZipFile(destination) as package:
                if package.testzip() is not None:
                    fail("invalid_office_package")
                for name in package.namelist():
                    if "vbaProject" in name or "externalLinks/" in name:
                        fail("unexpected_active_content")
        checks=["request_schema","template_sha256","utf8_roundtrip" if kind=="md" else "office_package_reopened"]
        if kind=="xlsx":
            checks += ["libreoffice_recalculated","formula_error_scan"]
            if request.get("budget"):
                checks += ["budget_line_amounts","budget_total"]
        if kind=="pptx":
            checks += ["text_capacity_bounds","table_pagination"]
        return {"success":True,"artifact":{"filename":filename,"mime_type":MIME[kind],"size":len(data),"sha256":hashlib.sha256(data).hexdigest(),"content_base64":base64.b64encode(data).decode("ascii")},"validation":{"passed":True,"checks":checks,"requires_visual_review":True}}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    result=generate(json.loads(pathlib.Path(args.input).read_text(encoding="utf-8")))
    pathlib.Path(args.output).write_text(json.dumps(result,ensure_ascii=False),encoding="utf-8")


if __name__=="__main__":
    main()
