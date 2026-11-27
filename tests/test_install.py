"""Runtime checks shared by the wheel and requirements-only install jobs."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from notebooklm_mcp_server import generate_styled_slides, pdf_to_png, png_to_pdf


class TestInstall(unittest.TestCase):
    def test_slide_styles(self):
        rules = {
            "corporate": "Tone: Authoritative, precise, information-dense, trustworthy.",
            "educational": "Tone: Warm, approachable, clear, encouraging.",
            "creative": "Tone: Bold, contemporary, confident, visually striking.",
        }
        for style, rule in rules.items():
            with self.subTest(style=style):
                prompt = generate_styled_slides("demo", style)
                self.assertEqual([line for line in prompt.splitlines() if line.startswith("Tone:")], [rule])

    def test_pdf_page_order(self):
        import fitz

        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "input.pdf"
            with fitz.open() as doc:
                for color in [(1, 0, 0), (0, 0, 1)]:
                    page = doc.new_page(width=72, height=72)
                    page.draw_rect(page.rect, color=color, fill=color)
                doc.save(pdf)

            split = asyncio.run(pdf_to_png(str(pdf), dpi=72))
            self.assertEqual(split["page_count"], 2)
            merged = asyncio.run(png_to_pdf(image_directory=split["output_directory"]))
            with fitz.open(merged["output_path"]) as doc:
                self.assertEqual([page.get_pixmap().pixel(36, 36) for page in doc], [(255, 0, 0), (0, 0, 255)])


if __name__ == "__main__":
    unittest.main()
