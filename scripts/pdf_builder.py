"""
pdf_builder.py — построение PDF lead magnet через ReportLab.

Архитектура PDF:
1. Cover page (обложка) — либо из assets/images/<creative>_card1.png (от M1),
   либо генерируем абстрактную через ReportLab
2. Inside pages — из markdown в assets/pdf_content/<creative>.md
3. CTA final page — либо из assets/images/<creative>_card3.png,
   либо генерируем через ReportLab с большой кнопкой

Markdown поддерживается ограниченно:
- # H1
- ## H2
- ### H3
- - bullet
- 1. numbered
- **bold**, *italic*
- > blockquote
- параграфы текста
- ![alt](path) — embed картинки
- --- разделитель страниц
"""

import re
from pathlib import Path
from typing import Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        Image,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError as e:
    raise ImportError(
        "reportlab не установлен. Запусти: pip install reportlab>=4.0"
    ) from e


# ============ Styles ============

def make_styles(brand: dict) -> dict:
    """Создаёт набор Paragraph styles с учётом бренд-цветов."""
    primary = HexColor(brand.get("primary_color", "#1E40AF"))
    accent = HexColor(brand.get("accent_color", "#22C55E"))
    dark = HexColor(brand.get("neutral_dark", "#0F172A"))

    base = getSampleStyleSheet()["Normal"]

    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=40,
            leading=46,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=20,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=base,
            fontName="Helvetica",
            fontSize=16,
            leading=22,
            textColor=colors.white,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "cover_brand": ParagraphStyle(
            "cover_brand",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=14,
            textColor=colors.white,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=28,
            leading=34,
            textColor=primary,
            spaceBefore=20,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=26,
            textColor=primary,
            spaceBefore=16,
            spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=dark,
            spaceBefore=12,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base,
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            textColor=dark,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base,
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            textColor=dark,
            leftIndent=20,
            bulletIndent=6,
            spaceAfter=4,
        ),
        "blockquote": ParagraphStyle(
            "blockquote",
            parent=base,
            fontName="Helvetica-Oblique",
            fontSize=11,
            leading=16,
            textColor=dark,
            leftIndent=18,
            borderColor=accent,
            borderWidth=0,
            spaceAfter=10,
        ),
        "cta_title": ParagraphStyle(
            "cta_title",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=32,
            leading=38,
            textColor=colors.white,
            alignment=TA_CENTER,
            spaceAfter=20,
        ),
        "cta_subtitle": ParagraphStyle(
            "cta_subtitle",
            parent=base,
            fontName="Helvetica",
            fontSize=14,
            leading=20,
            textColor=colors.white,
            alignment=TA_CENTER,
            spaceAfter=30,
        ),
        "_primary_color": primary,
        "_accent_color": accent,
        "_dark_color": dark,
    }


# ============ Markdown parser (минимальный) ============

INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
INLINE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def md_inline(text: str) -> str:
    """Inline markdown → reportlab paragraph markup."""
    text = INLINE_BOLD.sub(r"<b>\1</b>", text)
    text = INLINE_ITALIC.sub(r"<i>\1</i>", text)
    # ReportLab требует & в HTML entities
    text = text.replace("&", "&amp;")
    # Возвращаем bold/italic теги обратно (после escape & они стали &lt;b&gt;...)
    text = text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    text = text.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
    return text


def parse_markdown_to_blocks(md: str) -> list:
    """Парсит markdown в список блоков для дальнейшего рендера.

    Возвращает список dict: {"type": "h1/h2/h3/body/bullet/numbered/quote/image/break/page_break", "text": "...", "src": "..."}
    """
    blocks = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if not line:
            i += 1
            continue

        # Разделитель страниц
        if line.strip() == "---" or line.strip() == "***":
            blocks.append({"type": "page_break"})
            i += 1
            continue

        # Заголовки
        if line.startswith("### "):
            blocks.append({"type": "h3", "text": line[4:].strip()})
            i += 1
            continue
        if line.startswith("## "):
            blocks.append({"type": "h2", "text": line[3:].strip()})
            i += 1
            continue
        if line.startswith("# "):
            blocks.append({"type": "h1", "text": line[2:].strip()})
            i += 1
            continue

        # Картинки
        img_match = INLINE_IMAGE.match(line)
        if img_match:
            blocks.append({"type": "image", "alt": img_match.group(1), "src": img_match.group(2)})
            i += 1
            continue

        # Blockquote
        if line.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:].strip())
                i += 1
            blocks.append({"type": "quote", "text": " ".join(quote_lines)})
            continue

        # Numbered list
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i] or ""):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i]).strip())
                i += 1
            blocks.append({"type": "numbered", "items": items})
            continue

        # Bullet list
        if line.startswith("- ") or line.startswith("* "):
            items = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("* ")):
                items.append(lines[i][2:].strip())
                i += 1
            blocks.append({"type": "bullet", "items": items})
            continue

        # Параграф (может занимать несколько строк до пустой)
        para_lines = []
        while i < len(lines) and lines[i].strip() and not _is_special(lines[i]):
            para_lines.append(lines[i].strip())
            i += 1
        if para_lines:
            blocks.append({"type": "body", "text": " ".join(para_lines)})

    return blocks


def _is_special(line: str) -> bool:
    if not line.strip():
        return False
    if line.startswith("#"):
        return True
    if line.startswith("- ") or line.startswith("* "):
        return True
    if line.startswith("> "):
        return True
    if line.strip() == "---" or line.strip() == "***":
        return True
    if re.match(r"^\d+\.\s+", line):
        return True
    return False


# ============ Builder ============

def build_pdf(
    output_path: Path,
    title: str,
    subtitle: str,
    brand: dict,
    md_content: str,
    cover_image: Optional[Path] = None,
    cta_image: Optional[Path] = None,
    cta_url: Optional[str] = None,
    company_info: Optional[str] = None,
):
    """
    Собирает PDF lead magnet.

    Args:
        output_path: путь к .pdf
        title: главный заголовок
        subtitle: подзаголовок (под обложку)
        brand: brand config (product_name, primary_color, accent_color)
        md_content: markdown тело PDF
        cover_image: опциональная картинка обложки (от M1)
        cta_image: опциональная картинка CTA-страницы
        cta_url: ссылка для CTA в конце
        company_info: юр.инфо в футере
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = make_styles(brand)
    primary_color = styles["_primary_color"]
    accent_color = styles["_accent_color"]
    product_name = brand.get("product_name", "Product")

    # Document setup
    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=title,
        author=product_name,
    )

    # Page templates: cover (без полей), normal (с полями), cta (без полей)
    frame_full = Frame(0, 0, A4[0], A4[1], leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, showBoundary=0)
    frame_body = Frame(
        2 * cm, 2 * cm, A4[0] - 4 * cm, A4[1] - 4 * cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def draw_cover(canvas, doc):
        # Фоновая заливка
        canvas.setFillColor(primary_color)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        # Геометрический узор (декоративно)
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(colors.white)
        canvas.setLineWidth(0.5)
        canvas.setFillAlpha(0.05)
        for x in range(0, int(A4[0]), 30):
            canvas.line(x, 0, x, A4[1])
        canvas.setFillAlpha(1.0)
        # Brand name top-right
        canvas.setFont("Helvetica-Bold", 14)
        canvas.setFillColor(colors.white)
        canvas.drawRightString(A4[0] - 2 * cm, A4[1] - 2 * cm, product_name)

    def draw_normal(canvas, doc):
        # Header / footer normal pages
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(2 * cm, 1 * cm, product_name)
        canvas.drawRightString(A4[0] - 2 * cm, 1 * cm, f"стр. {doc.page}")
        if company_info:
            canvas.setFont("Helvetica", 7)
            canvas.drawString(2 * cm, 0.5 * cm, company_info[:120])

    def draw_cta(canvas, doc):
        canvas.setFillColor(accent_color)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_full], onPage=draw_cover),
        PageTemplate(id="normal", frames=[frame_body], onPage=draw_normal),
        PageTemplate(id="cta", frames=[frame_full], onPage=draw_cta),
    ])

    story = []

    # === Cover ===
    if cover_image and Path(cover_image).exists():
        # Используем картинку M1 как обложку
        story.append(Image(str(cover_image), width=A4[0], height=A4[1]))
    else:
        # Программно — большой заголовок на фоне primary_color (см. draw_cover)
        story.append(Spacer(1, 8 * cm))
        story.append(Paragraph(title, styles["cover_title"]))
        story.append(Paragraph(subtitle, styles["cover_subtitle"]))

    story.append(PageBreak())

    # Switch to normal template
    from reportlab.platypus.doctemplate import NextPageTemplate
    story.append(NextPageTemplate("normal"))

    # === Body (markdown) ===
    blocks = parse_markdown_to_blocks(md_content)
    for blk in blocks:
        btype = blk["type"]
        if btype == "page_break":
            story.append(PageBreak())
        elif btype == "h1":
            story.append(Paragraph(md_inline(blk["text"]), styles["h1"]))
        elif btype == "h2":
            story.append(Paragraph(md_inline(blk["text"]), styles["h2"]))
        elif btype == "h3":
            story.append(Paragraph(md_inline(blk["text"]), styles["h3"]))
        elif btype == "body":
            story.append(Paragraph(md_inline(blk["text"]), styles["body"]))
        elif btype == "bullet":
            for item in blk["items"]:
                story.append(Paragraph(f"• {md_inline(item)}", styles["bullet"]))
        elif btype == "numbered":
            for i, item in enumerate(blk["items"], 1):
                story.append(Paragraph(f"{i}. {md_inline(item)}", styles["bullet"]))
        elif btype == "quote":
            story.append(Paragraph(md_inline(blk["text"]), styles["blockquote"]))
        elif btype == "image":
            src = blk["src"]
            # Относительные пути — рассчитываем от рабочей папки кампании
            src_path = Path(src)
            if src_path.exists():
                try:
                    img = Image(str(src_path), width=A4[0] - 4 * cm, kind="proportional")
                    story.append(img)
                    story.append(Spacer(1, 6))
                except Exception:
                    pass  # пропускаем если не получается embed

    # === CTA page ===
    story.append(NextPageTemplate("cta"))
    story.append(PageBreak())

    if cta_image and Path(cta_image).exists():
        story.append(Image(str(cta_image), width=A4[0], height=A4[1]))
    else:
        story.append(Spacer(1, 9 * cm))
        story.append(Paragraph("Готовы начать?", styles["cta_title"]))
        if cta_url:
            story.append(Paragraph(f"Перейдите: <b>{cta_url}</b>", styles["cta_subtitle"]))
        story.append(Paragraph(f"Бесплатно до 5 человек • 14 дней триал на все возможности", styles["cta_subtitle"]))

    doc.build(story)
    return output_path
