import json
from pathlib import Path
import tempfile
import zipfile

from docx import Document
from pptx import Presentation


def main():
    output = Path("/workspace/output")
    if not output.is_dir():
        raise RuntimeError("SANDBOX_OUTPUT_DIRECTORY_REQUIRED")
    directory = Path(tempfile.mkdtemp(prefix="boyankb-office-probe-", dir=output))
    marker = "BoyanKB Office acceptance | 中文文档与演示文稿"
    document = Document()
    document.add_heading("BoyanKB 验收报告", level=1)
    document.add_paragraph(marker)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "结果"
    table.cell(1, 0).text = "离线生成"
    table.cell(1, 1).text = "待人工版式核验"
    docx_path = directory / "boyankb-office-probe.docx"
    document.save(docx_path)

    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.shapes.title.text = "BoyanKB 验收演示"
    first.placeholders[1].text = marker
    second = presentation.slides.add_slide(presentation.slide_layouts[1])
    second.shapes.title.text = "检查项目"
    second.placeholders[1].text = "中文字体\n文本未裁切\n下载后可编辑"
    pptx_path = directory / "boyankb-office-probe.pptx"
    presentation.save(pptx_path)

    reopened_doc = Document(docx_path)
    reopened_ppt = Presentation(pptx_path)
    with zipfile.ZipFile(docx_path) as archive:
        docx_zip_valid = archive.testzip() is None
    with zipfile.ZipFile(pptx_path) as archive:
        pptx_zip_valid = archive.testzip() is None
    checks = {
        "docx_reopened": any(marker in paragraph.text for paragraph in reopened_doc.paragraphs),
        "docx_table": len(reopened_doc.tables) == 1,
        "pptx_reopened": len(reopened_ppt.slides) == 2,
        "pptx_text": any(marker in shape.text for slide in reopened_ppt.slides for shape in slide.shapes if shape.has_text_frame),
        "docx_zip_valid": docx_zip_valid,
        "pptx_zip_valid": pptx_zip_valid,
    }
    passed = all(checks.values())
    print(json.dumps({"status": "PASS" if passed else "FAIL", "scope": "office_libraries_and_ooxml_only", "checks": checks, "files": [str(docx_path), str(pptx_path)]}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
