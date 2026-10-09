import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pptx import Presentation

import render
import render_pptx_v2 as v2
from test_render import TEMPLATES, artifact, request
from test_template_versions import install_v2_test_templates


OUTPUT = Path(os.environ.get("BOYAN_V2_RENDER_TEST_OUTPUT", str(Path(__file__).resolve().parents[2] / "temp/ppt-design-chinese/renderer")))


def sample_request(scene="teaching"):
    value = request("pptx", scene)
    value["template_version"] = "boyan-v2"
    value["document"] = {"title": "博研文化" + v2.SCENE_LABELS[scene] + "设计验证", "sections": [
        {"heading": "封面", "body": "封面附加说明必须完整可见，不因合并封面而丢失。", "notes": "封面补充讲解。"},
        {"heading": "课程目标与议程", "bullets": ["核对目标和实际资料。", "辨认教学内容与策划建议。", "确认后续行动和负责人。"], "notes": "本节总时长3分钟，不按页重复累计。"},
        {"heading": "核心概念说明", "body": "完整正文必须按原文保留。计划总时长45分钟，预算口径1,480.50元均为合成验证输入。TeachingQualityChecklist2026 与 Version2.10 不应截断。", "notes": "该节为排版测试文字。"},
        {"heading": "实施步骤", "bullets": ["第一阶段：核对已有资料与需求。", "第二阶段：按已确认内容组织执行。", "第三阶段：记录反馈并安排复核。"], "notes": "按三个阶段展开。"},
        {"heading": "互动练习", "body": "如何确认某项信息已有可靠来源？", "bullets": ["请对照原文说明判断依据。", "没有依据时保留待核实标记。"], "notes": "互动为策划建议。"},
        {"heading": "内容边界对比", "bullets": ["已有依据：当前授权原文中明确写出的内容。", "尚待核实：用户假设和创意建议不能改写为总部事实。"]},
        {"heading": "任务分工", "tables": [{"headers": ["环节", "任务", "负责人", "数量", "备注"], "rows": [[f"阶段{i + 1}", f"核对任务{i + 1}", "待指定", str(i + 1), "合成验证数据"] for i in range(18)]}], "notes": "表格18行必须完整，五列均需保留。"},
        {"heading": "总结与后续行动", "body": "保留全部原始内容和真实来源。确认完成后再交付，并继续核实待定事项。", "notes": "总结原有内容，不新增承诺。"},
        {"heading": "资料来源", "body": "以下是模板验证资料，不是实际业务事实。", "bullets": ["来源说明必须保留。"], "notes": "来源附加备注也必须保留。"},
    ]}
    return value


def slide_layout(slide):
    names = {shape.name for shape in slide.shapes}
    for index, marker in ((0, "cover-title"), (1, "point-0"), (2, "body"), (3, "step-title-0"), (4, "question-body"), (5, "left-body"), (6, "content-table"), (7, "summary-body")):
        if marker in names:
            return index
    raise AssertionError("unexpected generated layout")


def visible_text(slide):
    return "".join(shape.text.replace("\n", "") if shape.has_text_frame else "".join(cell.text.replace("\n", "") for row in shape.table.rows for cell in row.cells) if shape.has_table else "" for shape in slide.shapes)


class RenderV2Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        install_v2_test_templates(self.root)
        patched = patch.object(render, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def check_content(self, deck, value):
        text = "".join(visible_text(slide) for slide in deck.slides)
        notes = "\n".join(slide.notes_slide.notes_text_frame.text for slide in deck.slides)
        self.assertEqual(sum(slide_layout(slide) == 0 for slide in deck.slides), 1)
        self.assertEqual({slide_layout(slide) for slide in deck.slides}, set(range(8)))
        self.assertNotIn("模板占位文字", text)
        for section in value["document"]["sections"]:
            for unit in v2.content_units(section):
                self.assertIn(unit.replace("\n", ""), text)
            if section.get("notes"):
                self.assertIn(section["notes"], notes)
        for slide in deck.slides:
            self.assertIn("template-test-source", slide.notes_slide.notes_text_frame.text)
        self.assertIn("1,480.50元", text)
        self.assertIn("45分钟", text)
        self.assertIn("TeachingQualityChecklist2026", text)
        self.assertIn("Version2.10", text)
        self.assertIn("核对任务18", text)
        self.assertLessEqual(len(deck.slides), 80)
        for slide in deck.slides:
            for shape in slide.shapes:
                if shape.has_text_frame and shape.name not in {"number-0", "number-1", "number-2"} and shape.text:
                    self.assertTrue(v2.fits(shape, shape.text), shape.name)
                if shape.has_table:
                    self.assertLessEqual(shape.height, v2.validate_template(self.root / "ppt-v2" / (value["scene"] + ".pptx")).slides[6].shapes[-1].height)
                    self.assertTrue(all(run.font.size.pt >= 18 for row in shape.table.rows for cell in row.cells for paragraph in cell.text_frame.paragraphs for run in paragraph.runs))

    def test_eight_layouts_text_tables_and_notes(self):
        value = sample_request()
        result = render.generate(value)
        content = artifact(result)
        self.check_content(Presentation(io.BytesIO(content)), value)

    def test_long_chinese_ascii_and_numeric_text_is_complete(self):
        value = request()
        value["template_version"] = "boyan-v2"
        body = "TeachingQualityChecklist2026，预计45分钟，测算1,480.50元。" + "中文教学内容必须完整保留并安全分页，不改变原始事实。" * 90
        value["document"]["sections"] = [{"heading": "长段落", "body": body, "notes": "整节预计时长45分钟，不按页累计。"}]
        value.pop("budget")
        deck = Presentation(io.BytesIO(artifact(render.generate(value))))
        parts = []
        pages = []
        for slide in deck.slides:
            named = {shape.name: shape for shape in slide.shapes}
            if "heading" in named and named["heading"].text.startswith("长段落"):
                parts.append(named["body"].text.replace("\n", ""))
                pages.append(slide)
        self.assertGreater(len(parts), 2)
        self.assertEqual("".join(parts), body)
        for index, slide in enumerate(pages, 1):
            self.assertIn(f"本节共 {len(pages)} 页，当前第 {index}/{len(pages)} 页。", slide.notes_slide.notes_text_frame.text)
            self.assertIn(value["document"]["sections"][0]["notes"], slide.notes_slide.notes_text_frame.text)
        self.assertTrue(any("1,480.50元" in part for part in parts))
        self.assertTrue(any("TeachingQualityChecklist2026" in part for part in parts))

    def test_invalid_layout_and_unfittable_word_fail_closed(self):
        value = sample_request()
        value["document"]["sections"] = [{"heading": "长单词", "body": "A" * 500}]
        with self.assertRaisesRegex(render.RenderError, "presentation_text_does_not_fit"):
            render.generate(value)
        template = self.root / "ppt-v2/teaching.pptx"
        deck = Presentation(template)
        next(shape for shape in deck.slides[2].shapes if shape.name == "body").name = "missing-body"
        deck.save(template)
        with self.assertRaisesRegex(render.RenderError, "invalid_presentation_layouts"):
            v2.validate_template(template)

    def test_cover_section_keeps_visible_content_tables_and_original_notes(self):
        for body, merged in (("这份课件说明目标受众与资料边界。", True), ("原始封面说明必须完整显示，不能以省略号代替。" * 20, False)):
            with self.subTest(merged=merged):
                value = request()
                value["template_version"] = "boyan-v2"
                value["document"]["sections"] = [{"heading": "封面", "body": body, "notes": "原始封面讲师备注。", "tables": [{"headers": ["字段", "值"], "rows": [["说明", "封面表格也保留"]]}]}]
                value.pop("budget")
                deck = Presentation(io.BytesIO(artifact(render.generate(value))))
                subtitles = [shape.text for shape in deck.slides[0].shapes if shape.name == "cover-subtitle"]
                if merged:
                    self.assertEqual(subtitles[0].replace("\n", ""), body)
                    self.assertIn("封面\n原始封面讲师备注。", deck.slides[0].notes_slide.notes_text_frame.text)
                else:
                    pages = [slide for slide in deck.slides if any(shape.name == "heading" and shape.text.startswith("课件说明") for shape in slide.shapes)]
                    self.assertEqual("".join(shape.text.replace("\n", "") for slide in pages for shape in slide.shapes if shape.name == "body"), body)
                    self.assertIn("封面\n原始封面讲师备注。", pages[0].notes_slide.notes_text_frame.text)
                self.assertIn("封面表格也保留", "".join(visible_text(slide) for slide in deck.slides))
                self.assertFalse(any(shape.name == "heading" and shape.text == "封面" for slide in deck.slides for shape in slide.shapes))


@unittest.skipUnless((TEMPLATES / "ppt-v2/manifest.json").is_file(), "PPT v2 design templates are unavailable")
class ActualV2TemplateTests(unittest.TestCase):
    def test_three_scenes_generate_editable_presentations(self):
        reports = []
        OUTPUT.mkdir(parents=True, exist_ok=True)
        with patch.object(render, "ROOT", TEMPLATES):
            for scene in render.ACCENTS:
                with self.subTest(scene=scene):
                    value = sample_request(scene)
                    result = render.generate(value)
                    content = artifact(result)
                    path = OUTPUT / (scene + ".pptx")
                    path.write_bytes(content)
                    (OUTPUT / (scene + ".request.json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                    deck = Presentation(io.BytesIO(content))
                    self.assertEqual({slide_layout(slide) for slide in deck.slides}, set(range(8)))
                    text = "".join(visible_text(slide) for slide in deck.slides)
                    for section in value["document"]["sections"]:
                        for unit in v2.content_units(section):
                            self.assertIn(unit.replace("\n", ""), text)
                    with zipfile.ZipFile(TEMPLATES / "ppt-v2" / (scene + ".pptx")) as original, zipfile.ZipFile(io.BytesIO(content)) as generated:
                        original_media = [name for name in original.namelist() if name.startswith("ppt/media/")]
                        generated_media = [name for name in generated.namelist() if name.startswith("ppt/media/")]
                        self.assertLessEqual(len(generated_media), len(original_media))
                    reports.append({"scene": scene, "template_version": "boyan-v2", "filename": path.name, "sha256": hashlib.sha256(content).hexdigest(), "pages": len(deck.slides), "layouts": sorted({slide_layout(slide) for slide in deck.slides}), "table_pages": sum(any(shape.has_table for shape in slide.shapes) for slide in deck.slides), "media_parts": len(generated_media), "text_preserved": True, "native_office_review": "pending"})
        (OUTPUT / "report.json").write_text(json.dumps({"mode": "non_llm_render_fixtures", "results": reports}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
