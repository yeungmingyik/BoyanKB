import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pptx import Presentation
from pptx.util import Inches, Pt

import render
import server
from test_render import TEMPLATES, request


def install_v2_test_templates(root):
    target = root / "ppt-v2"
    target.mkdir()
    entries = []
    layouts = [
        {"cover-title": (72, 194, 650, 184, 60), "cover-subtitle": (76, 412, 620, 100, 24), "cover-context": (76, 616, 1050, 28, 18)},
        {"point-" + str(i): (160, 239 + i * 116, 650, 82, 28) for i in range(3)},
        {"body": (64, 232, 704, 338, 28)},
        {**{"step-title-" + str(i): (64 + i * 398, 306, 348, 200, 28) for i in range(3)}, "process-note": (64, 556, 1148, 52, 22)},
        {"question-body": (72, 236, 768, 210, 34), "question-hint": (76, 496, 754, 94, 24)},
        {"left-heading": (64, 230, 526, 58, 30), "right-heading": (692, 230, 526, 58, 30), "left-body": (64, 308, 526, 280, 26), "right-body": (692, 308, 526, 280, 26)},
        {"table-note": (64, 603, 1152, 36, 18)},
        {"summary-body": (72, 246, 790, 290, 30), "closing-note": (76, 575, 790, 50, 20)},
    ]
    for scene in render.ACCENTS:
        deck = Presentation()
        deck.slide_width, deck.slide_height = Inches(1280 / 96), Inches(720 / 96)
        for index, fields in enumerate(layouts):
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            fields = dict(fields)
            if index:
                fields.update({"heading": (64, 108, 1148, 100, 40), "page": (1160, 660, 50, 30, 16), "section-label": (64, 40, 700, 35, 18)})
            for name, (x, y, width, height, size) in fields.items():
                shape = slide.shapes.add_textbox(*(Inches(v / 96) for v in (x, y, width, height)))
                shape.name = name
                shape.text = "模板占位文字"
                shape.text_frame.paragraphs[0].runs[0].font.size = Pt(size * .75)
            if index == 6:
                shape = slide.shapes.add_table(2, 2, *(Inches(v / 96) for v in (64, 228, 1152, 356)))
                shape.name = ""
        filename = scene + ".pptx"
        deck.save(target / filename)
        entries.append({"id": scene + "-pptx", "scene": scene, "format": "pptx", "file": filename, "sha256": hashlib.sha256((target / filename).read_bytes()).hexdigest()})
    manifest = {"schema_version": 1, "version": "boyan-v2", "brand_id": "boyan-culture", "templates": entries}
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return target


class TemplateVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        manifest = json.loads((TEMPLATES / "manifest.json").read_text(encoding="utf-8"))
        shutil.copyfile(TEMPLATES / "manifest.json", self.root / "manifest.json")
        for entry in manifest["templates"]:
            shutil.copyfile(TEMPLATES / entry["file"], self.root / entry["file"])
        self.original = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        patched = patch.object(render, "ROOT", self.root)
        patched.start()
        self.addCleanup(patched.stop)

    def install_v2(self):
        return install_v2_test_templates(self.root)

    def test_versioned_registry_preserves_v1(self):
        legacy = render.validate(request())
        target = self.install_v2()
        value = request()
        value["template_version"] = "boyan-v2"
        self.assertEqual(render.validate(value), target / "teaching.pptx")
        self.assertEqual(render.validate(request()), legacy)
        self.assertEqual(render.template_capabilities(), {"boyan-v1": ["docx", "pptx", "xlsx", "md"], "boyan-v2": ["pptx"]})
        self.assertEqual(self.original, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir() if path.is_file()})

    def test_missing_unknown_and_wrong_format_fail_closed(self):
        for version in ("boyan-v2", "boyan-v3", "../boyan-v1", None, []):
            with self.subTest(version=version):
                value = request()
                value["template_version"] = version
                with self.assertRaises(render.RenderError):
                    render.validate(value)
        self.install_v2()
        for kind in ("docx", "xlsx", "md"):
            value = request(kind)
            value["template_version"] = "boyan-v2"
            with self.assertRaisesRegex(render.RenderError, "invalid_brand_version"):
                render.validate(value)

    def test_changed_v2_digest_fails_health_without_falling_back(self):
        target = self.install_v2()
        (target / "teaching.pptx").write_bytes(b"changed")
        value = request()
        value["template_version"] = "boyan-v2"
        with self.assertRaisesRegex(render.RenderError, "template_integrity_failed"):
            render.validate(value)
        with self.assertRaisesRegex(render.RenderError, "template_integrity_failed"):
            server.readiness()
        self.assertEqual(render.validate(request()), self.root / "teaching.pptx")

    def test_health_keeps_legacy_and_reports_available_versions(self):
        with patch.object(server.shutil, "which", return_value="/usr/bin/libreoffice"):
            legacy = server.readiness()
            self.assertEqual(legacy["template_version"], "boyan-v1")
            self.assertEqual(legacy["ppt_template_versions"], ["boyan-v1"])
            self.install_v2()
            current = server.readiness()
        self.assertEqual(current["template_version"], legacy["template_version"])
        self.assertEqual(current["formats"], legacy["formats"])
        self.assertEqual(current["template_versions"]["boyan-v2"], ["pptx"])
        self.assertEqual(current["ppt_template_versions"], ["boyan-v1", "boyan-v2"])


if __name__ == "__main__":
    unittest.main()
