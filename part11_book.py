"""
Twin Shadow — Part 11: Children's Book Generator
=================================================
Combines fantm.ink story generation with FAL image generation
to produce illustrated children's books as PDF files.

Templates:
  1. Classic   — text above or below illustration (stacked layout)
  2. Immersive — full-bleed image with white text-box overlay

Output:
  - Book PDF         (8.5" × 8.5" square, one page per PDF page)
  - Instructions PDF (A4, printing + binding guide + supply list)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import requests as _req

from part2_router import call_task

log = logging.getLogger("tsai.book")

# ---------------------------------------------------------------------------
# IMAGE STYLE SUFFIXES
# ---------------------------------------------------------------------------

IMAGE_STYLES: dict[str, str] = {
    "watercolor": "children's book watercolor illustration, soft pastel colors, whimsical, dreamy, no text",
    "cartoon":    "children's book cartoon illustration, bold outlines, bright saturated colors, playful, no text",
    "pencil":     "children's book pencil and ink illustration, detailed linework, gentle color wash, no text",
    "digital":    "children's book digital art illustration, vibrant colors, clean shapes, modern, no text",
}

# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class BookPage:
    num:          int
    text:         str
    image_prompt: str
    image_path:   str | None = None


@dataclass
class ChildrensBook:
    title:       str
    author:      str
    pages:       list[BookPage] = field(default_factory=list)
    template:    int = 1          # 1 = Classic, 2 = Immersive
    text_pos:    str = "below"    # template-1: above / below / alternating
    image_style: str = "watercolor"

# ---------------------------------------------------------------------------
# STORY SPLITTER
# ---------------------------------------------------------------------------

_SPLIT_SYSTEM = """You are a children's book editor.
Given a story concept and total page count, create page content for the full book.
Return ONLY a valid JSON array — no markdown, no explanation.

Each element:
  {"text": "...", "image_prompt": "..."}

Rules:
- text: 1–4 short sentences, age 4–8 reading level.
- image_prompt: vivid visual scene description for an illustrator.
  Describe what is visible. No character names — describe appearance instead.
- Page 1 (index 0): the opening scene / cover page image.
- Last page: a satisfying, warm ending.
- Every page must advance the story."""


async def split_story(concept: str, title: str, num_pages: int) -> list[dict]:
    """Use AI to split a concept into pages with text + image prompts."""
    prompt = (
        f'Title: "{title}"\nConcept: {concept}\n'
        f'Total pages needed (including cover and ending): {num_pages}\n\n'
        f'Create exactly {num_pages} pages. Return JSON array only.'
    )
    messages = [
        {"role": "system", "content": _SPLIT_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    try:
        raw = await call_task("creative", messages)
        clean = raw.strip()
        if "```" in clean:
            lines = clean.split("\n")
            clean = "\n".join(l for l in lines if not l.strip().startswith("```"))
        data = json.loads(clean)
        if not isinstance(data, list):
            raise ValueError("Expected list")
        return data[:num_pages]
    except Exception as e:
        log.error("[BOOK] split_story failed: %s", e)
        fallback = []
        for i in range(num_pages):
            label = "Once upon a time..." if i == 0 else ("The End." if i == num_pages - 1 else f"Page {i + 1}.")
            fallback.append({
                "text": label,
                "image_prompt": f"illustration for page {i + 1} of a children's book called {title}",
            })
        return fallback

# ---------------------------------------------------------------------------
# IMAGE DOWNLOAD HELPER
# ---------------------------------------------------------------------------

def download_image_to(url: str, dest_path: str) -> str | None:
    """Download a URL to dest_path. Returns dest_path on success, None on failure."""
    try:
        r = _req.get(url, timeout=90)
        r.raise_for_status()
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return dest_path
    except Exception as e:
        log.error("[BOOK] image download failed: %s", e)
        return None

# ---------------------------------------------------------------------------
# SHARED PDF HELPERS
# ---------------------------------------------------------------------------

_W = 216.0   # page width  mm (≈ 8.5 in)
_H = 216.0   # page height mm (≈ 8.5 in)
_M = 10.0    # margin mm
_TH1 = 68.0  # text-area height for template-1 story pages
_TH2 = 60.0  # white-box  height for template-2 story pages


def _new_pdf():
    from fpdf import FPDF
    pdf = FPDF(unit="mm", format=(_W, _H))
    pdf.set_auto_page_break(False)
    pdf.set_margins(0, 0, 0)
    return pdf


def _place_image(pdf, path: str | None, x: float, y: float, w: float, h: float):
    if path and Path(path).exists():
        try:
            pdf.image(path, x=x, y=y, w=w, h=h)
            return
        except Exception as e:
            log.warning("[BOOK] image embed failed: %s", e)
    # Purple placeholder
    pdf.set_fill_color(44, 0, 70)
    pdf.rect(x, y, w, h, style="F")


def _text_block(pdf, text: str, x: float, y: float, w: float, h: float,
                size: int = 15, bold: bool = False, color=(30, 0, 50)):
    style = "B" if bold else ""
    pdf.set_font("Times", style, size)
    pdf.set_text_color(*color)
    line_h = size * 0.45
    # Estimate lines to center vertically
    pdf.set_xy(x, y)
    lines = pdf.multi_cell(w, line_h, text, align="C", split_only=True)
    total_h = len(lines) * line_h
    top_pad = max(0.0, (h - total_h) / 2.0)
    pdf.set_xy(x, y + top_pad)
    pdf.multi_cell(w, line_h, text, align="C")


# ---------------------------------------------------------------------------
# TEMPLATE 1 — Classic (text above OR below image)
# ---------------------------------------------------------------------------

def build_pdf_template1(book: ChildrensBook, output_path: str) -> str:
    pdf  = _new_pdf()
    img_h = _H - _TH1        # image zone height (~148 mm)
    n     = len(book.pages)

    for i, page in enumerate(book.pages):
        pdf.add_page()
        is_cover = (i == 0)
        is_last  = (i == n - 1)

        if is_cover:
            # Full image top portion, white strip for title/author at bottom
            cover_img_h = _H - 38
            _place_image(pdf, page.image_path, 0, 0, _W, cover_img_h)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, cover_img_h, _W, 38, style="F")
            _text_block(pdf, book.title,           _M, cover_img_h + 2,  _W - 2*_M, 22, size=22, bold=True,  color=(40, 0, 80))
            _text_block(pdf, f"by {book.author}",  _M, cover_img_h + 24, _W - 2*_M, 12, size=12, bold=False, color=(80, 40, 110))

        elif is_last:
            end_img_h = _H - 30
            _place_image(pdf, page.image_path, 0, 0, _W, end_img_h)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, end_img_h, _W, 30, style="F")
            _text_block(pdf, "The End", _M, end_img_h + 4, _W - 2*_M, 22, size=22, bold=True, color=(40, 0, 80))

        else:
            pos = book.text_pos
            if pos == "alternating":
                pos = "above" if i % 2 == 0 else "below"

            if pos == "above":
                # White text area top, image below
                pdf.set_fill_color(255, 255, 255)
                pdf.rect(0, 0, _W, _TH1, style="F")
                _text_block(pdf, page.text, _M, _M, _W - 2*_M, _TH1 - 2*_M, size=15)
                _place_image(pdf, page.image_path, 0, _TH1, _W, img_h)
            else:
                # Image top, white text area bottom
                _place_image(pdf, page.image_path, 0, 0, _W, img_h)
                pdf.set_fill_color(255, 255, 255)
                pdf.rect(0, img_h, _W, _TH1, style="F")
                _text_block(pdf, page.text, _M, img_h + _M, _W - 2*_M, _TH1 - 2*_M, size=15)

    pdf.output(output_path)
    return output_path


# ---------------------------------------------------------------------------
# TEMPLATE 2 — Immersive (full-bleed image + white text-box overlay)
# ---------------------------------------------------------------------------

def build_pdf_template2(book: ChildrensBook, output_path: str) -> str:
    pdf = _new_pdf()
    n   = len(book.pages)

    for i, page in enumerate(book.pages):
        pdf.add_page()
        is_cover = (i == 0)
        is_last  = (i == n - 1)

        # Full-bleed image always
        _place_image(pdf, page.image_path, 0, 0, _W, _H)

        if is_cover:
            box_h = 42
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, _H - box_h, _W, box_h, style="F")
            _text_block(pdf, book.title,          _M, _H - box_h + 2,  _W - 2*_M, 26, size=22, bold=True,  color=(40, 0, 80))
            _text_block(pdf, f"by {book.author}", _M, _H - box_h + 28, _W - 2*_M, 12, size=12, bold=False, color=(80, 40, 110))

        elif is_last:
            box_h = 34
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, _H - box_h, _W, box_h, style="F")
            _text_block(pdf, "The End", _M, _H - box_h + 4, _W - 2*_M, 26, size=24, bold=True, color=(40, 0, 80))

        else:
            # White box at bottom
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, _H - _TH2, _W, _TH2, style="F")
            _text_block(pdf, page.text, _M, _H - _TH2 + _M, _W - 2*_M, _TH2 - 2*_M, size=15)

    pdf.output(output_path)
    return output_path


# ---------------------------------------------------------------------------
# INSTRUCTIONS PDF
# ---------------------------------------------------------------------------

def build_instructions_pdf(book: ChildrensBook, output_path: str) -> str:
    from fpdf import FPDF

    pdf = FPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    m = 20
    W = pdf.w

    def _h(text: str, size: int = 14, bold: bool = True):
        pdf.set_font("Times", "B" if bold else "", size)
        pdf.set_text_color(60, 0, 100)
        pdf.set_x(m)
        pdf.multi_cell(W - 2*m, 7, text, align="L")
        pdf.ln(2)

    def _p(text: str, size: int = 12):
        pdf.set_font("Times", "", size)
        pdf.set_text_color(30, 0, 50)
        pdf.set_x(m)
        pdf.multi_cell(W - 2*m, 6, text, align="L")
        pdf.ln(1)

    def _bullets(items: list[str]):
        pdf.set_font("Times", "", 12)
        pdf.set_text_color(30, 0, 50)
        for item in items:
            pdf.set_x(m + 4)
            pdf.multi_cell(W - 2*m - 4, 6, f"\u2022  {item}", align="L")
        pdf.ln(3)

    tpl  = "Classic (Text + Image Stacked)" if book.template == 1 else "Immersive (Full-Bleed Image)"
    ppos = f"Text position: {book.text_pos.title()}" if book.template == 1 else ""
    n    = len(book.pages)

    _h(f'"{book.title}"', size=20)
    _p(f"by {book.author}", size=13)
    pdf.ln(4)

    _h("BOOK DETAILS", 13)
    _bullets([
        f"Template: {tpl}",
        f"Pages: {n} (including cover and ending)",
        f"Image style: {book.image_style.title()}",
        ppos or "",
        "Format: 8.5\" \u00d7 8.5\" square",
    ])

    _h("PRINT SETTINGS", 13)
    _bullets([
        "Paper size: 8.5\" \u00d7 8.5\" square",
        "  Option A: Buy pre-cut 8.5\u00d7 8.5\" paper from a craft store",
        "  Option B: Print on 8.5\" \u00d7 11\" Letter, trim 1.25\" from one end after printing",
        "Color mode: Full color — use your printer's best quality / photo setting",
        "Resolution: 300 DPI or higher",
        "Margins: None / Borderless — disable all auto-scaling",
        "Duplex printing: flip on SHORT edge for correct two-sided layout",
        "Print cover page separately on heavier cardstock",
    ])

    _h("SUPPLY LIST", 13)
    _bullets([
        "Inkjet or laser printer with color ink/toner",
        f"Plain copy paper 80gsm+ — {n - 1} sheets (interior pages, duplex)",
        "1 sheet of white cardstock 200gsm+ (cover)",
        "Long-arm stapler or bookbinding saddle-stitch kit",
        "Bone folder or butter knife (for crisp folds)",
        "Paper cutter or sharp scissors + metal ruler",
        "Optional: laminator pouch (Letter size) for cover protection",
        "Optional: binder clips to hold pages while the glue/staple sets",
    ])

    _h("ASSEMBLY STEPS", 13)
    _bullets([
        "1. Print the cover page (page 1) on cardstock. Trim to 8.5\" \u00d7 8.5\".",
        "2. Print interior pages on copy paper (duplex, flip on short edge).",
        "3. Collate all pages in order. Place the cover on the outside.",
        "4. Fold the full stack in half — this forms the spine of the booklet.",
        "5. Use a bone folder to sharpen and flatten the fold.",
        "6. Open the booklet flat on a hard surface. "
           "Saddle-stitch (staple) along the center spine — two staples.",
        "7. Close the booklet and press firmly. Trim any uneven edges with a cutter.",
        "8. Optional: laminate the cover before assembly for long-term durability.",
    ])

    _h("PRO TIPS", 13)
    _bullets([
        "Print one test page first to verify image quality and color accuracy.",
        "Set your print dialog to 'Actual Size' — do NOT 'Fit to Page'.",
        "For a premium result: take the PDF to FedEx Office, Staples, or a local print shop.",
        "Ask for a 'saddle-stitch booklet' if ordering professionally.",
        "Cardstock covers feel much more like a real book — worth the extra effort.",
    ])

    pdf.output(output_path)
    return output_path
