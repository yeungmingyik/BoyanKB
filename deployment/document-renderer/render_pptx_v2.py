import math
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import MSO_ANCHOR
from pptx.util import Pt

import render as core


LAYOUT_FIELDS = {
    0: ("cover-title", "cover-subtitle", "cover-context"),
    1: ("heading", "point-0", "point-1", "point-2"),
    2: ("heading", "body"),
    3: ("heading", "step-title-0", "step-title-1", "step-title-2", "process-note"),
    4: ("heading", "question-body", "question-hint"),
    5: ("heading", "left-heading", "right-heading", "left-body", "right-body"),
    6: ("heading", "content-table", "table-note"),
    7: ("heading", "summary-body", "closing-note"),
}
SCENE_LABELS = {"teaching": "教学课件", "business": "商务方案", "operations": "运营执行"}
SOURCE_HEADINGS = {"资料来源", "参考资料", "引用来源", "来源"}
COVER_HEADINGS = {"封面", "课件封面", "课程封面", "标题页"}


def validate_template(path):
    deck = Presentation(path)
    if len(deck.slides) != len(LAYOUT_FIELDS):
        core.fail("invalid_presentation_layouts")
    for index, required in LAYOUT_FIELDS.items():
        if index == 6:
            tables = [shape for shape in deck.slides[index].shapes if shape.has_table]
            if len(tables) != 1:
                core.fail("invalid_presentation_layouts")
            tables[0].name = "content-table"
        shapes = {shape.name: shape for shape in deck.slides[index].shapes}
        for name in required + (() if index == 0 else ("page", "section-label")):
            shape = shapes.get(name)
            if shape is None or (name == "content-table" and not shape.has_table) or (name != "content-table" and not shape.has_text_frame):
                core.fail("invalid_presentation_layouts")
    return deck


def font_size(shape):
    return next((run.font.size.pt for paragraph in shape.text_frame.paragraphs for run in paragraph.runs if run.font.size), 24)


def fits(shape, value, minimum=None):
    existing = font_size(shape)
    width = (shape.width - shape.text_frame.margin_left - shape.text_frame.margin_right) / 12700
    height = (shape.height - shape.text_frame.margin_top - shape.text_frame.margin_bottom) / 12700
    for size in [existing] + ([minimum] if minimum and minimum < existing else []):
        capacity = width / (size * 1.08)
        lines = core.wrap_text(value, capacity)
        if all(core.text_width(line) <= capacity for line in lines) and len(lines) * size * (1.24 if shape.name == "heading" else 1.12) <= height:
            return True
    return False


def text_pages(shape, value):
    result = []
    while value:
        if fits(shape, value):
            result.append(value)
            break
        boundaries = core.text_boundaries(value)
        low, high, last = 0, len(boundaries) - 1, -1
        while low <= high:
            middle = (low + high) // 2
            if fits(shape, value[:boundaries[middle]]):
                last = middle
                low = middle + 1
            else:
                high = middle - 1
        if last < 0:
            core.fail("presentation_text_does_not_fit")
        maximum = boundaries[last]
        target = math.ceil(len(value) / math.ceil(len(value) / maximum))
        choices = [index for index in boundaries[:last + 1] if index >= max(1, min(target, maximum) // 2) and len(value) - index >= 12]

        def priority(index):
            prefix = value[:index].rstrip()
            weight = 0 if value[index - 1] == "\n" else 1 if re.search(r"[。！？.!?；;][）】》”’]*$", prefix) else 2 if re.search(r"[，：、,:][）】》”’]*$", prefix) else 3 if value[index - 1].isspace() else 4
            return weight, abs(index - target), index

        cut = min(choices, key=priority) if choices else maximum
        if not fits(shape, value[:cut]):
            cut = maximum
        result.append(value[:cut])
        value = value[cut:]
    return result or [""]


def content_units(section):
    units = core.paragraphs(section.get("body", ""))
    return units + list(section.get("bullets") or [])


def layout_for(section):
    heading = section["heading"].strip()
    if heading in COVER_HEADINGS:
        return 2
    if re.search("对比|比较|区别|差异|两类", heading):
        return 5
    if re.search("步骤|流程|阶段|执行计划|实施路径", heading):
        return 3
    if re.search("互动|练习|讨论|思考|案例|问题", heading):
        return 4
    if re.search("总结|小结|后续|行动|结束|下一步", heading):
        return 7
    if re.search("目标|议程|概览|目录|要点", heading):
        return 1
    return 2


def table_pages(shape, headers, rows):
    size = 18
    capacity = (shape.width / 12700 / len(headers) - 12) / (size * 1.08)
    available = shape.height / 12700

    def height(values):
        lines = [core.wrap_text(value, capacity) for value in values]
        if any(core.text_width(line) > capacity for group in lines for line in group):
            core.fail("presentation_table_cell_too_long")
        return max(len(group) for group in lines) * size * 1.15 + 12

    header = height(headers)
    if header > available * .4:
        core.fail("presentation_table_header_too_long")
    page, heights, used = [], [], header
    for row in rows:
        row_height = height(row)
        if header + row_height > available:
            core.fail("presentation_table_cell_too_long")
        if page and (used + row_height > available or len(page) >= 6):
            yield page, [header] + heights, size, capacity
            page, heights, used = [], [], header
        page.append(row)
        heights.append(row_height)
        used += row_height
    if page or not rows:
        yield page, [header] + heights, size, capacity


def render_pptx_v2(request, template, destination):
    deck = validate_template(template)
    originals = list(deck.slides)
    original_names = [{shape.name: shape for shape in slide.shapes} for slide in originals]
    created = []
    sources = "\n".join(core.source_lines(request))

    def create(index, heading=""):
        if len(created) >= 80:
            core.fail("presentation_too_large")
        slide = core.clone_slide(deck, originals[index])
        created.append(slide)
        named = {shape.name: shape for shape in slide.shapes}
        for name in LAYOUT_FIELDS[index]:
            if name != "content-table":
                core.set_text(named[name], "")
        if index:
            core.fitted_text(named["heading"], heading, font_size(named["heading"]) * .8)
            core.set_text(named["section-label"], SCENE_LABELS[request["scene"]])
        return slide, named

    def notes(slides, value, group="本节", timing=True):
        for index, slide in enumerate(slides, 1):
            parts = []
            if len(slides) > 1:
                parts.append(f"{group}共 {len(slides)} 页，当前第 {index}/{len(slides)} 页。")
                if value and timing:
                    parts.append("以下讲师备注中的预计时长为整节总时长，不逐页累加。")
            parts.extend(part for part in (value, sources) if part)
            slide.notes_slide.notes_text_frame.text = "\n".join(parts)

    def text_slides(heading, text, layout=2):
        field = "summary-body" if layout == 7 else "body"
        result = []
        for index, part in enumerate(text_pages(original_names[layout][field], text)):
            slide, named = create(layout, heading + ("（续）" if index else ""))
            core.fitted_text(named[field], part)
            result.append(slide)
        return result

    def section_pages(section):
        heading = section["heading"]
        units = content_units(section)
        text = "\n".join(units)
        layout = layout_for(section)
        if not units:
            return [] if section.get("tables") else text_slides(heading, "")
        if layout == 7:
            return text_slides(heading, text, 7)
        if layout == 4:
            if not fits(original_names[4]["question-body"], units[0]):
                return text_slides(heading, text)
            slide, named = create(4, heading)
            core.fitted_text(named["question-body"], units[0])
            remaining = "\n".join(units[1:])
            if fits(named["question-hint"], remaining):
                core.fitted_text(named["question-hint"], remaining)
                return [slide]
            return [slide] + text_slides(heading + "（续）", remaining)
        if layout == 5 and len(units) == 2 and all(fits(original_names[5][name], value) for name, value in zip(("left-body", "right-body"), units)):
            slide, named = create(5, heading)
            for index, side in enumerate(("left", "right")):
                core.set_text(named[side + "-heading"], "对照" + ("一" if index == 0 else "二"))
                core.fitted_text(named[side + "-body"], units[index])
            return [slide]
        if layout in (1, 3):
            prefix = "point-" if layout == 1 else "step-title-"
            if all(fits(original_names[layout][prefix + str(index % 3)], value) for index, value in enumerate(units)):
                result = []
                for offset, group in core.point_pages(units):
                    page_heading = heading + ("（续）" if offset else "")
                    if len(group) != 3:
                        result.extend(text_slides(page_heading, "\n".join(group)))
                        continue
                    slide, named = create(layout, page_heading)
                    for index, value in enumerate(group):
                        core.fitted_text(named[prefix + str(index)], value)
                    result.append(slide)
                return result
        return text_slides(heading, text)

    def append_table(heading, headers, rows):
        result = []
        placeholder = original_names[6]["content-table"]
        for index, (page_rows, heights, size, capacity) in enumerate(table_pages(placeholder, headers, rows)):
            slide, named = create(6, heading + ("（续）" if index else ""))
            old = named["content-table"]
            left, top, width = old.left, old.top, old.width
            old.element.getparent().remove(old.element)
            shape = slide.shapes.add_table(len(page_rows) + 1, len(headers), left, top, width, int(sum(heights) * 12700))
            shape.name = "content-table"
            for ri, values in enumerate([headers] + page_rows):
                shape.table.rows[ri].height = int(heights[ri] * 12700)
                for ci, value in enumerate(values):
                    cell = shape.table.cell(ri, ci)
                    cell.text = "\n".join(core.wrap_text(value, capacity))
                    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                    cell.margin_left = cell.margin_right = Pt(6)
                    cell.margin_top = cell.margin_bottom = Pt(4)
                    cell.text_frame.word_wrap = False
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor.from_string(core.ACCENTS[request["scene"]] if ri == 0 else "F0F5FB" if ri % 2 == 0 else "FFFFFF")
                    for paragraph in cell.text_frame.paragraphs:
                        paragraph.space_before = paragraph.space_after = Pt(0)
                        paragraph.line_spacing = 1.05
                        for run in paragraph.runs:
                            run.font.name = core.FONT
                            run.font.size = Pt(size)
                            run.font.bold = ri == 0
                            run.font.color.rgb = RGBColor.from_string("FFFFFF" if ri == 0 else "182A43")
            result.append(slide)
        return result

    cover, named = create(0)
    title = request["document"]["title"]
    core.fitted_text(named["cover-title"], title, font_size(named["cover-title"]) * .75)
    core.fitted_text(named["cover-subtitle"], SCENE_LABELS[request["scene"]])
    notes([cover], "", timing=False)
    source_sections = []
    for section_index, original_section in enumerate(request["document"]["sections"]):
        section = original_section
        if section["heading"].strip() in SOURCE_HEADINGS:
            source_sections.append(section)
            continue
        merged_cover = False
        if section["heading"].strip() in COVER_HEADINGS:
            section = dict(section)
            section["notes"] = "\n".join(value for value in (original_section["heading"], original_section.get("notes", "")) if value)
            section["heading"] = "课件说明" if request["scene"] == "teaching" else "方案说明"
            content = "\n".join(content_units(section))
            if section_index == 0 and fits(named["cover-subtitle"], content):
                if content:
                    core.fitted_text(named["cover-subtitle"], content)
                merged_cover = True
        pages = [cover] if merged_cover else section_pages(section)
        for table in section.get("tables") or []:
            pages.extend(append_table(section["heading"], table["headers"], table["rows"]))
        notes(pages, section.get("notes", ""))
    if request.get("budget"):
        rows = [[item["label"], str(item["quantity"]), f"{item['unit_price']:.2f}", str(core.line_amount(item))] for item in request["budget"]["items"]]
        rows.append(["预算合计", "", "", str(sum(core.line_amount(item) for item in request["budget"]["items"]))])
        pages = append_table("预算测算", ["项目", "数量", "单价（元）", "金额（元）"], rows)
        notes(pages, "预算假设须核实，不能作为总部正式报价。", "预算测算", False)
    source_text = []
    source_notes = []
    source_tables = []
    for section in source_sections:
        source_text.extend(content_units(section))
        source_notes.append(section.get("notes", ""))
        source_tables.extend(section.get("tables") or [])
    source_text.extend(f"[{index}] {source['title']}" for index, source in enumerate(request["sources"], 1))
    pages = text_slides("资料来源", "\n".join(source_text))
    for table in source_tables:
        pages.extend(append_table("资料来源", table["headers"], table["rows"]))
    notes(pages, "\n".join(source_notes), "资料来源", False)
    for index in range(len(originals) - 1, -1, -1):
        item = deck.slides._sldIdLst[index]
        deck.part.drop_rel(item.rId)
        deck.slides._sldIdLst.remove(item)
    for index, slide in enumerate(deck.slides, 1):
        for shape in slide.shapes:
            if shape.name == "page":
                core.set_text(shape, str(index).zfill(2))
    deck.core_properties.title = title
    deck.core_properties.author = "博研文化"
    deck.save(destination)
    Presentation(destination)
