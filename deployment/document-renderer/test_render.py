import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from docx import Document
from openpyxl import load_workbook
from pptx import Presentation

import render


REPO = Path(__file__).resolve().parents[2]
TEMPLATES = Path(os.environ.get("BOYAN_TEMPLATE_ROOT", str(REPO / "branding/templates")))
OUTPUT = Path(os.environ.get("BOYAN_RENDER_TEST_OUTPUT", str(REPO / "temp/brand-template-build/generated")))


def request(kind="pptx", scene="teaching"):
    return {
        "job_id": str(uuid.uuid4()), "tenant_id": 10001,
        "format": kind, "scene": scene, "brand_id": "boyan-culture",
        "template_version": "boyan-v1", "outline_hash": "a" * 64,
        "document": {"title": "合作伙伴模板验证", "sections": [
            {"heading": "学习目标与资料边界", "body": "本文件用于验证模板，不代表实际业务承诺。", "bullets": ["了解资料来源", "区分事实与假设", "确认下一步安排"], "notes": "讲师提示：说明资料边界，预计十分钟。"},
            {"heading": "活动任务与分工", "body": "按实际需求确定负责人。", "tables": [{"headers": ["阶段", "主要任务", "负责人"], "rows": [["筹备", "核实时间与资料", "待指定"], ["执行", "按已确认方案实施", "待指定"], ["复盘", "记录问题并跟进", "待指定"]]}]},
        ]},
        "sources": [{"id": "template-test-source", "chunk_id": "template-test-source", "title": "模板验证资料", "excerpt": "这是合成测试资料，不用于业务事实。"}],
        "budget": {"items": [{"label": "讲义费用【待核实】", "quantity": 12, "unit_price": 10}, {"label": "场地费用【待核实】", "quantity": 1, "unit_price": 1000}, {"label": "耗材费用【待核实】", "quantity": 12, "unit_price": 30}]},
    }


def artifact(result):
    content = base64.b64decode(result["artifact"]["content_base64"], validate=True)
    if hashlib.sha256(content).hexdigest() != result["artifact"]["sha256"]:
        raise AssertionError("artifact digest mismatch")
    return content


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.root_patch = patch.object(render, "ROOT", TEMPLATES)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def save(self, content, name):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / name).write_bytes(content)

    def test_three_scenes_word_ppt_and_markdown(self):
        for scene in ("teaching", "business", "operations"):
            for kind in ("docx", "pptx", "md"):
                with self.subTest(scene=scene, kind=kind):
                    result = render.generate(request(kind, scene))
                    content = artifact(result)
                    self.save(content, scene + "." + kind)
                    self.assertTrue(result["validation"]["requires_visual_review"])
                    if kind == "docx":
                        doc = Document(io.BytesIO(content))
                        text = "\n".join(p.text for p in doc.paragraphs)
                        self.assertIn("讲师提示", text)
                        self.assertIn("1480", text)
                    elif kind == "pptx":
                        deck = Presentation(io.BytesIO(content))
                        for slide in deck.slides:
                            self.assertFalse(slide.element.xpath(".//a:p[count(a:pPr)>1]"))
                        text = "\n".join(shape.text if shape.has_text_frame else "\n".join(cell.text for row in shape.table.rows for cell in row.cells) if shape.has_table else "" for slide in deck.slides for shape in slide.shapes)
                        self.assertIn("预算测算", text)
                        self.assertIn("1480", text)
                        self.assertIn("资料来源", text)
                        self.assertTrue(any("预计十分钟" in slide.notes_slide.notes_text_frame.text for slide in deck.slides if slide.has_notes_slide))
                    else:
                        text = content.decode("utf-8")
                        self.assertIn("博研文化", text)
                        self.assertIn("1480", text)
                        self.assertIn("utf8_roundtrip", result["validation"]["checks"])
                        self.assertNotIn("native_file_reopened", result["validation"]["checks"])

    def test_request_types_fail_closed(self):
        mutations = [
            lambda v: v.update(format=[]), lambda v: v.update(scene=[]), lambda v: v.update(outline_hash=[]),
            lambda v: v.update(sources=[None]), lambda v: v.update(sources=["text"]),
            lambda v: v["document"]["sections"][0].update(tables=[None]),
            lambda v: v["document"]["sections"][0].update(bullets=False),
            lambda v: v["budget"].update(items=[None]), lambda v: v["budget"]["items"][0].update(quantity=True),
            lambda v: v["budget"]["items"][0].update(unit_price=float("nan")),
            lambda v: v.update(code="__import__('os')"), lambda v: v.update(tenant_id=True),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = request()
                mutation(value)
                with self.assertRaises(render.RenderError):
                    render.validate(value)

    def test_presentation_long_titles_tables_and_sources_fit(self):
        value = request()
        value["document"]["title"] = "教学内容与业务资料边界确认" * 2
        value["document"]["sections"][0]["heading"] = "课程实施前需要共同确认的教学目标与课程资料范围及案例使用边界"
        value["document"]["sections"][1]["tables"] = [{"headers": ["栏目" + str(i) for i in range(8)], "rows": [["需要核对的资料和使用范围" + str(i) for i in range(8)] for _ in range(12)]}]
        value["sources"][0]["title"] = "资料来源与使用边界" * 18
        result = render.generate(value)
        content = artifact(result)
        self.save(content, "teaching-long-content.pptx")
        deck = Presentation(io.BytesIO(content))
        self.assertLessEqual(len(deck.slides), 80)
        table_slides = 0
        for slide in deck.slides:
            for shape in slide.shapes:
                if shape.has_table:
                    table_slides += 1
                    self.assertLess(shape.top + shape.height, deck.slide_height * .88)
                    for row in shape.table.rows:
                        for cell in row.cells:
                            text_size = max((run.font.size.pt for p in cell.text_frame.paragraphs for run in p.runs), default=0)
                            self.assertLessEqual(len(cell.text.splitlines()) * text_size * 1.05 + 8, row.height / 12700 + 1)
                if shape.has_text_frame and shape.name in {"heading", "cover-title", "point-0", "point-1", "point-2"}:
                    size = max((run.font.size.pt for p in shape.text_frame.paragraphs for run in p.runs if run.font.size), default=0)
                    self.assertLessEqual(len(shape.text.splitlines()) * size * 1.12, shape.height / 12700 + 1)
        self.assertGreater(table_slides, 2)

    def test_unbounded_presentation_is_rejected_early(self):
        value = request()
        value["document"]["sections"] = [{"heading": "长内容", "body": "资料" * 5000} for _ in range(3)]
        with self.assertRaisesRegex(render.RenderError, "presentation_too_large"):
            render.generate(value)

    def test_presentation_title_limit_is_checked_before_rendering(self):
        value = request()
        value["document"]["title"] = "课" * 35 + "😀"
        render.validate(value)
        value["document"]["title"] += "字"
        with self.assertRaisesRegex(render.RenderError, "presentation_title_too_long"):
            render.validate(value)

    def test_presentation_notes_group_body_and_table_continuations(self):
        value = request()
        notes = "讲师说明：练习后讨论，预计时长8分钟。"
        value["document"]["sections"] = [
            {"heading": "综合教学章节", "body": "这是需要展开讲解的课程说明。" * 50, "notes": notes,
             "tables": [{"headers": ["阶段", "练习任务"], "rows": [[str(i), "按要求记录练习结果"] for i in range(13)]}]},
            {"heading": "单页总结", "body": "确认理解后结束本节。", "notes": "预计时长2分钟。"},
        ]
        value["budget"]["items"] = [{"label": "费用" + str(i), "quantity": 1, "unit_price": 10} for i in range(13)]
        content = artifact(render.generate(value))
        self.save(content, "teaching-grouped-notes.pptx")
        deck = Presentation(io.BytesIO(content))
        groups = {"综合教学章节": [], "单页总结": [], "预算测算": []}
        for slide in deck.slides:
            heading = next((shape.text.replace("\n", "") for shape in slide.shapes if shape.name == "heading"), "")
            for key in groups:
                if heading.startswith(key):
                    groups[key].append(slide)
        section = groups["综合教学章节"]
        self.assertGreater(sum(any(shape.has_table for shape in slide.shapes) for slide in section), 1)
        self.assertGreater(sum(not any(shape.has_table for shape in slide.shapes) for slide in section), 1)
        for index, slide in enumerate(section, 1):
            text = slide.notes_slide.notes_text_frame.text
            self.assertIn(f"本节共 {len(section)} 页，当前第 {index}/{len(section)} 页。", text)
            self.assertIn("预计时长为整节总时长，不逐页累加", text)
            self.assertIn(notes, text)
            self.assertIn("template-test-source", text)
            self.assertEqual(text.count(notes), 1)
        self.assertEqual(len(groups["单页总结"]), 1)
        single = groups["单页总结"][0].notes_slide.notes_text_frame.text
        self.assertIn("预计时长2分钟。", single)
        self.assertNotIn("本节共", single)
        self.assertNotIn("不逐页累加", single)
        self.assertIn("template-test-source", single)
        self.assertGreater(len(groups["预算测算"]), 1)
        for index, slide in enumerate(groups["预算测算"], 1):
            text = slide.notes_slide.notes_text_frame.text
            self.assertIn(f"预算测算共 {len(groups['预算测算'])} 页，当前第 {index}/{len(groups['预算测算'])} 页。", text)
            self.assertNotIn("时长", text)
            self.assertIn("预算假设须核实", text)
            self.assertIn("template-test-source", text)

    def test_markdown_preserves_code_as_literal_and_escapes_html(self):
        value = request("md")
        value["document"]["sections"][0]["body"] = "<script>run()</script> ![remote](https://example.test/pixel) __import__('os')"
        result = artifact(render.generate(value)).decode("utf-8")
        self.assertIn("&lt;script&gt;", result)
        self.assertNotIn("<script>", result)
        self.assertNotIn("![remote]", result)
        self.assertIn("__import__('os')", result)

    def test_bad_template_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "manifest.json").write_bytes((TEMPLATES / "manifest.json").read_bytes())
            (target / "teaching.md").write_text("modified", encoding="utf-8")
            with patch.object(render, "ROOT", target):
                with self.assertRaisesRegex(render.RenderError, "template_integrity_failed"):
                    render.generate(request("md"))

    @unittest.skipUnless(shutil.which("libreoffice") or shutil.which("soffice"), "LibreOffice is unavailable on this test host")
    def test_spreadsheets_recalculate_and_replace_template_validation(self):
        for scene in ("teaching", "business", "operations"):
            value = request("xlsx", scene)
            value["document"]["sections"][0]["heading"] = "=UNTRUSTED()"
            result = render.generate(value)
            content = artifact(result)
            self.save(content, scene + ".xlsx")
            formulas = load_workbook(io.BytesIO(content), data_only=False)
            values = load_workbook(io.BytesIO(content), data_only=True)
            sheet = formulas.active
            total = next(cell for row in sheet for cell in row if isinstance(cell.value, str) and cell.value.startswith("=SUM("))
            self.assertEqual(values.active[total.coordinate].value, 1480)
            self.assertFalse(any(cell.data_type == "e" for row in values.active for cell in row))
            self.assertFalse(any(cell.value == "=UNTRUSTED()" and cell.data_type == "f" for row in sheet for cell in row))
            rules = sheet.data_validations.dataValidation
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0].type, "decimal")
            self.assertEqual(len(sheet.conditional_formatting), 0)
            self.assertIn("budget_line_amounts", result["validation"]["checks"])
        value = request("xlsx", "operations")
        value["budget"]["items"][0]["unit_price"] = .335
        content = artifact(render.generate(value))
        self.save(content, "operations-rounding.xlsx")
        workbook = load_workbook(io.BytesIO(content), data_only=True)
        self.assertTrue(any(cell.value == 1364.02 for row in workbook.active for cell in row))


if __name__ == "__main__":
    unittest.main()
