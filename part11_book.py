"""
Twin Shadow — Part 11: Children's Book Generator
=================================================
Combines fantm.ink story generation with FAL image generation
to produce illustrated children's books as PDF files.

Templates:
  1. Classic   — text above or below illustration (stacked layout)
  2. Immersive — full-bleed image with white text-box overlay

Paper / fold options (each PDF page = one booklet leaf, printed 2-up then folded):
  letter  — print on 8.5"×11", fold → 5.5"×8.5" booklet  (US standard)
  tabloid — print on 11"×17", fold → 8.5"×11" booklet     (US large)
  a5      — print on A4,       fold → A5 booklet            (148×210 mm)
  a4      — print on A3,       fold → A4 booklet            (210×297 mm)

Output:
  - Book PDF         (portrait leaf size, one page per PDF page)
  - Instructions PDF (A4, folded-booklet print guide + supply list)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import requests as _req

from part2_router import call_task

log = logging.getLogger("tsai.book")

# ---------------------------------------------------------------------------
# PAPER SIZES  (leaf dimensions = half of the print sheet, in mm)
# ---------------------------------------------------------------------------

PAPER_SIZES: dict[str, dict] = {
    "letter": {
        "w": 139.7, "h": 215.9,
        "sheet": '8.5" × 11" (US Letter)',
        "label": '5.5" × 8.5" booklet',
        "sheet_w": '8.5"', "sheet_h": '11"',
        "a_size": False,
    },
    "tabloid": {
        "w": 215.9, "h": 279.4,
        "sheet": '11" × 17" (Tabloid / Ledger)',
        "label": '8.5" × 11" booklet',
        "sheet_w": '11"', "sheet_h": '17"',
        "a_size": False,
    },
    "a5": {
        "w": 148.0, "h": 210.0,
        "sheet": "A4 (210 × 297 mm)",
        "label": "A5 booklet (148 × 210 mm)",
        "sheet_w": "210 mm", "sheet_h": "297 mm",
        "a_size": True,
    },
    "a4": {
        "w": 210.0, "h": 297.0,
        "sheet": "A3 (297 × 420 mm)",
        "label": "A4 booklet (210 × 297 mm)",
        "sheet_w": "297 mm", "sheet_h": "420 mm",
        "a_size": True,
    },
}

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
    template:    int = 1            # 1 = Classic, 2 = Immersive
    text_pos:    str = "below"      # template-1: above / below / alternating
    image_style: str = "watercolor"
    paper_size:  str = "letter"     # key into PAPER_SIZES

    @property
    def dims(self) -> dict:
        return PAPER_SIZES.get(self.paper_size, PAPER_SIZES["letter"])

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

_M = 10.0   # margin mm (all sizes)


def _new_pdf(W: float, H: float):
    from fpdf import FPDF
    pdf = FPDF(unit="mm", format=(W, H))
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
    pdf.set_fill_color(44, 0, 70)
    pdf.rect(x, y, w, h, style="F")


def _text_block(pdf, text: str, x: float, y: float, w: float, h: float,
                size: int = 15, bold: bool = False, color=(30, 0, 50)):
    style = "B" if bold else ""
    pdf.set_font("Times", style, size)
    pdf.set_text_color(*color)
    line_h = size * 0.45
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
    d   = book.dims
    W, H = d["w"], d["h"]
    # Text strip = ~30% of height; image zone = ~70%
    TH  = round(H * 0.30)
    pdf = _new_pdf(W, H)
    n   = len(book.pages)

    for i, page in enumerate(book.pages):
        pdf.add_page()
        is_cover = (i == 0)
        is_last  = (i == n - 1)

        if is_cover:
            strip_h = round(H * 0.175)
            img_h   = H - strip_h
            _place_image(pdf, page.image_path, 0, 0, W, img_h)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, img_h, W, strip_h, style="F")
            _text_block(pdf, book.title,          _M, img_h + 2,           W - 2*_M, strip_h * 0.6,
                        size=20, bold=True,  color=(40, 0, 80))
            _text_block(pdf, f"by {book.author}", _M, img_h + strip_h*0.62, W - 2*_M, strip_h * 0.35,
                        size=12, bold=False, color=(80, 40, 110))

        elif is_last:
            strip_h = round(H * 0.14)
            img_h   = H - strip_h
            _place_image(pdf, page.image_path, 0, 0, W, img_h)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, img_h, W, strip_h, style="F")
            _text_block(pdf, "The End", _M, img_h + 2, W - 2*_M, strip_h - 4,
                        size=20, bold=True, color=(40, 0, 80))

        else:
            img_h = H - TH
            pos   = book.text_pos
            if pos == "alternating":
                pos = "above" if i % 2 == 0 else "below"

            if pos == "above":
                pdf.set_fill_color(255, 255, 255)
                pdf.rect(0, 0, W, TH, style="F")
                _text_block(pdf, page.text, _M, _M, W - 2*_M, TH - 2*_M, size=14)
                _place_image(pdf, page.image_path, 0, TH, W, img_h)
            else:
                _place_image(pdf, page.image_path, 0, 0, W, img_h)
                pdf.set_fill_color(255, 255, 255)
                pdf.rect(0, img_h, W, TH, style="F")
                _text_block(pdf, page.text, _M, img_h + _M, W - 2*_M, TH - 2*_M, size=14)

    pdf.output(output_path)
    return output_path


# ---------------------------------------------------------------------------
# TEMPLATE 2 — Immersive (full-bleed image + white text-box overlay)
# ---------------------------------------------------------------------------

def build_pdf_template2(book: ChildrensBook, output_path: str) -> str:
    d   = book.dims
    W, H = d["w"], d["h"]
    TH2 = round(H * 0.28)   # white box height
    pdf = _new_pdf(W, H)
    n   = len(book.pages)

    for i, page in enumerate(book.pages):
        pdf.add_page()
        is_cover = (i == 0)
        is_last  = (i == n - 1)

        _place_image(pdf, page.image_path, 0, 0, W, H)

        if is_cover:
            box_h = round(H * 0.195)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, H - box_h, W, box_h, style="F")
            _text_block(pdf, book.title,          _M, H - box_h + 2,           W - 2*_M, box_h * 0.62,
                        size=20, bold=True,  color=(40, 0, 80))
            _text_block(pdf, f"by {book.author}", _M, H - box_h + box_h*0.65, W - 2*_M, box_h * 0.32,
                        size=12, bold=False, color=(80, 40, 110))

        elif is_last:
            box_h = round(H * 0.16)
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, H - box_h, W, box_h, style="F")
            _text_block(pdf, "The End", _M, H - box_h + 2, W - 2*_M, box_h - 4,
                        size=20, bold=True, color=(40, 0, 80))

        else:
            pdf.set_fill_color(255, 255, 255)
            pdf.rect(0, H - TH2, W, TH2, style="F")
            _text_block(pdf, page.text, _M, H - TH2 + _M, W - 2*_M, TH2 - 2*_M, size=14)

    pdf.output(output_path)
    return output_path


# ---------------------------------------------------------------------------
# INSTRUCTIONS PDF  (folded-booklet guide)
# ---------------------------------------------------------------------------

def build_instructions_pdf(book: ChildrensBook, output_path: str) -> str:
    from fpdf import FPDF

    pdf = FPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    m = 20
    W = pdf.w

    def _h(text: str, size: int = 14):
        pdf.set_font("Times", "B", size)
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
            if not item:
                continue
            pdf.set_x(m + 4)
            pdf.multi_cell(W - 2*m - 4, 6, f"\u2022  {item}", align="L")
        pdf.ln(3)

    def _note(text: str):
        pdf.set_font("Times", "I", 11)
        pdf.set_text_color(80, 40, 110)
        pdf.set_x(m + 4)
        pdf.multi_cell(W - 2*m - 4, 6, text, align="L")
        pdf.ln(2)

    d    = book.dims
    tpl  = "Classic (Text + Image Stacked)" if book.template == 1 else "Immersive (Full-Bleed Image)"
    ppos = f"Text position: {book.text_pos.title()}" if book.template == 1 else ""
    n    = len(book.pages)
    # Sheets needed: each sheet folds to give 2 leaves (front + back = 2 pages each side)
    # For saddle-stitch: sheets = ceil(n / 4) — but home-folder uses n/2 sheets stacked
    sheets_interior = (n - 1 + 1) // 2   # interior leaves (excluding cover which is 1 sheet)
    sheets_total    = (n + 3) // 4        # for true saddle-stitch imposition
    # Simpler: just say n/2 sheets for folded stack method
    stack_sheets = (n + 1) // 2

    _h(f'"{book.title}"', size=20)
    _p(f"by {book.author}", size=13)
    pdf.ln(4)

    # ── Book details ──────────────────────────────────────────────────────────
    _h("BOOK DETAILS", 13)
    _bullets([
        f"Template: {tpl}",
        f"Pages: {n} (cover + story + ending)",
        f"Image style: {book.image_style.title()}",
        ppos,
        f"Finished booklet size: {d['label']}",
        f"Print sheet: {d['sheet']}",
    ])

    # ── How folded booklets work ──────────────────────────────────────────────
    _h("HOW THIS BOOKLET WORKS", 13)
    _p(
        "Each page of this PDF is one leaf (one side) of your finished booklet. "
        "You print two pages per sheet of paper — one on each half — then fold "
        "the stack in half along the spine to create a proper bound booklet."
    )
    pdf.ln(2)

    # ── Paper options ─────────────────────────────────────────────────────────
    _h("PAPER OPTIONS — CHOOSE ONE", 13)

    _p("OPTION A — US Letter (recommended minimum):")
    _bullets([
        "Print sheet: 8.5\" \u00d7 11\"",
        "Finished booklet leaf: 5.5\" \u00d7 8.5\" (half of letter, folded on the long edge)",
        "Feels like a standard digest-size children's book",
        "Works on any home inkjet or laser printer",
    ])

    _p("OPTION B — Tabloid / Ledger (larger, more impressive):")
    _bullets([
        "Print sheet: 11\" \u00d7 17\"",
        "Finished booklet leaf: 8.5\" \u00d7 11\" (half of tabloid, folded on the long edge)",
        "Full US letter size — images are larger and text is easier to read",
        "Requires a wide-format printer or a print shop",
    ])

    _p("OPTION C — A4 \u2192 A5 (international):")
    _bullets([
        "Print sheet: A4 (210 \u00d7 297 mm)",
        "Finished booklet leaf: A5 (148 \u00d7 210 mm) — fold on long edge",
        "Standard international stationery; available everywhere",
    ])

    _p("OPTION D — A3 \u2192 A4 (international large):")
    _bullets([
        "Print sheet: A3 (297 \u00d7 420 mm)",
        "Finished booklet leaf: A4 (210 \u00d7 297 mm) — fold on long edge",
        "Large-format; use a print shop or wide-format printer",
    ])

    _note(
        f"This PDF was generated at {d['label']} leaf size. "
        f"To use a different paper option, regenerate the book and select the matching paper format."
    )

    # ── Print settings ────────────────────────────────────────────────────────
    _h("PRINT SETTINGS", 13)
    _bullets([
        f"Paper: load {d['sheet']} in your printer",
        "Scaling: Actual Size — do NOT enable 'Fit to Page' or 'Shrink to Fit'",
        "Margins: None / Borderless if your printer supports it",
        "Color: Full color, Best Quality / Photo mode",
        "Sides: Two-sided (duplex) — flip on the LONG edge",
        "Pages per sheet: 2 — place two PDF pages side-by-side on each sheet",
        "  (In your print dialog: 'Pages per sheet: 2', layout: left-to-right)",
        "Print in booklet order if your software supports 'Booklet' print mode",
        "Cover: print the first page on cardstock (200gsm+) for a stiff cover",
    ])

    _note(
        "Tip: Adobe Acrobat Reader's 'Booklet' print mode automatically handles "
        "page imposition (which pages print side-by-side). Use it if available."
    )

    # ── Supply list ───────────────────────────────────────────────────────────
    _h("SUPPLY LIST", 13)
    _bullets([
        "Inkjet or laser printer (wide-format for Tabloid/A3 options)",
        f"Plain copy paper 80gsm+ — {stack_sheets} sheets for interior pages",
        "1 sheet white cardstock 200gsm+ for the cover",
        "Long-arm stapler or bookbinding saddle-stitch kit (staples reach the spine)",
        "Bone folder or butter knife — for sharp, clean folds",
        "Paper cutter or sharp scissors + metal ruler — for trimming edges",
        "Optional: laminator + Letter/A4 pouch — seal the cover for durability",
        "Optional: binder clips — hold the stack flat while stapling",
        "Optional: PVA glue + clamp — for a glued-spine perfect-bound finish",
    ])

    # ── Assembly steps ────────────────────────────────────────────────────────
    _h("ASSEMBLY STEPS", 13)
    _bullets([
        f"1. Print the cover (PDF page 1) on cardstock, 2-up on one {d['sheet']} sheet.",
        "2. Print all remaining PDF pages 2-up on copy paper (duplex, flip on long edge).",
        "3. Collate the printed sheets in page order. Place the cover sheet on the outside.",
        "4. Fold the entire stack in half along the long edge — this is your booklet spine.",
        "5. Use a bone folder to press and sharpen the fold from the inside out.",
        "6. Hold the stack firmly. Open the booklet flat on a hard surface.",
        "7. Saddle-stitch: drive two staples through the center spine fold, evenly spaced.",
        "   (Or use a bookbinding kit with thread/tape for a cleaner finish.)",
        "8. Close the booklet and press firmly under a heavy book for 10–15 minutes.",
        "9. Trim the three open edges with a paper cutter for a clean, square finish.",
        "10. Optional: laminate the cover before assembly for a professional look.",
    ])

    # ── Pro tips ──────────────────────────────────────────────────────────────
    _h("PRO TIPS", 13)
    _bullets([
        "Print one test sheet first — check colour, scaling, and page order before the full run.",
        "Adobe Acrobat Reader > Print > 'Booklet' handles page imposition automatically.",
        "FedEx Office, Staples, and local print shops can print and bind professionally.",
        "Ask for 'saddle-stitch booklet printing' and specify your paper size.",
        "Cardstock covers make a world of difference — don't skip them.",
        "A laminated cover survives little hands much better than plain paper.",
    ])

    pdf.output(output_path)
    return output_path
