"""
prompt_templates.py — шаблоны промптов для генерации картинок через OpenAI.

5 типов креативов:
- ebook_cover — обложка PDF lead magnet
- pain_split — split-screen «до / после»
- abstract_brand — абстрактная брендовая композиция с текстом
- carousel_card — карточка карусели (1 из N)
- ui_mockup — UI-скриншот продукта (placeholder, требует реальный)

Каждый шаблон умеет:
- Принимать контекст (brand, текст, концепцию)
- Возвращать готовый prompt-строку для OpenAI Images API
"""

from textwrap import dedent


# ============ EBOOK COVER ============

EBOOK_COVER = dedent("""
    Professional B2B SaaS eBook cover image for the title: "{cover_title}".

    Visual style:
    - Modern flat illustration, vector-style, no photorealism
    - Brand color palette: primary {primary_color}, accent {accent_color}
    - Background: solid colored block (use {primary_color}) with subtle geometric pattern (dots, lines)
    - Top-left: large bold typography of the cover title "{cover_title}"
    - Top-right: small product logo placeholder (text "{product_name}")
    - Bottom-right: abstract illustration representing {visual_concept}
    - Style references: Airtable eBook covers, Notion templates, Linear documentation

    Composition:
    - Aspect ratio: 4:5 portrait
    - Clean, generous white space
    - Bold corporate typography
    - Easily readable at thumbnail size in social feed

    DO NOT include:
    - People or faces
    - Realistic photographs
    - More than 8 words of body text
    - Logos of real third-party brands
""").strip()


# ============ PAIN / REFRAME SPLIT-SCREEN ============

PAIN_SPLIT = dedent("""
    Split-screen B2B SaaS advertising image showing "before vs after" contrast.

    LEFT HALF (50% — "before / problem"):
    - Chaotic, cluttered desktop scene
    - Multiple browser tabs overlapping, scattered sticky notes
    - Overflowing email inbox icon, missed-deadline calendar
    - Conflicting Excel cells with red error highlights
    - Color tone: muted, slightly desaturated, beige/grey palette
    - Conveys: stress, confusion, lost tasks

    RIGHT HALF (50% — "after / solution with {product_name}"):
    - Clean, organized single-screen interface
    - Orderly kanban board with neat columns
    - Single focused window, no distractions
    - Calm satisfied feeling
    - Color tone: vibrant brand palette — primary {primary_color}, accent {accent_color}
    - Conveys: clarity, control, calm productivity

    CENTER:
    - Thin vertical divider line with subtle gradient
    - Optional small arrow or transition indicator

    TOP OVERLAY:
    - Large bold text: "{pain_headline}"
    - Text color: high contrast, readable on both halves

    Style: flat vector illustration with subtle shadows, B2B SaaS aesthetic.
    Aspect ratio: 4:5 portrait (suitable for VK ad).
    No real human faces. No real third-party product names.
""").strip()


# ============ ABSTRACT BRAND ============

ABSTRACT_BRAND = dedent("""
    Modern B2B SaaS social media ad image for {product_name}.

    Composition:
    - Central message: bold typographic text "{main_text}"
    - Background: abstract geometric composition — connected dots, flowing lines, network nodes suggesting workflow and team collaboration
    - Brand colors: primary {primary_color} (~60%), accent {accent_color} (~30%), white/light grey (~10%)
    - Small product wordmark in bottom-right corner: "{product_name}"

    Style:
    - Notion / Linear / Airtable aesthetic
    - Clean, minimal, modern
    - Soft gradients allowed
    - Flat vector illustration

    Aspect ratio: 1:1 square (1080x1080).

    DO NOT include:
    - People, faces, hands
    - Realistic photos
    - More than 12 words total
    - Logos of real third-party brands
""").strip()


# ============ CAROUSEL CARD ============

CAROUSEL_CARD = dedent("""
    B2B SaaS carousel card {index} of {total} — "{card_title}".

    Composition:
    - Background: solid brand color block (use {primary_color} or {accent_color})
    - Center: large abstract icon representing the concept "{concept}"
    - Top: small card-number badge "{index}/{total}"
    - Middle-large: bold heading "{card_title}"
    - Bottom-small: body line "{card_body}"
    - Bottom corner: small product wordmark "{product_name}"

    Style:
    - Minimalist, flat design
    - Consistent typography across the carousel series
    - Easily readable on mobile thumbnail size
    - {product_name} brand aesthetic
    - Smooth visual progression — this is card {index}, viewer will see {previous_index} before and {next_index} after

    Aspect ratio: 1:1 square (1080x1080).

    DO NOT include:
    - People, faces
    - Realistic photos
    - More than 15 words total per card
""").strip()


# ============ EBOOK CAROUSEL — отдельные шаблоны для разных карточек ============

EBOOK_PREVIEW_PAGE = dedent("""
    Inside-page preview of a B2B SaaS eBook for {product_name}.

    Composition:
    - Mockup of an open PDF page seen from a slight 3D angle (or flat top-down view)
    - On the page: a list of 5-7 short bullet points or a 2x2 grid of conceptual icons representing "{visual_concept}"
    - Brand colors: primary {primary_color}, accent {accent_color}
    - White page background with subtle drop shadow
    - Small "{product_name}" wordmark in page header
    - No real readable text — abstract typographic lines suggesting text

    Style:
    - Clean editorial / report aesthetic (think McKinsey / Harvard Business Review report)
    - Flat illustration with subtle shadows
    - Bold but minimal

    Aspect ratio: 4:5 portrait.

    DO NOT include:
    - People or faces
    - Realistic photographs
    - More than 10 short text fragments
    - Logos of real third-party brands
""").strip()


EBOOK_CTA_CARD = dedent("""
    Final call-to-action card of a B2B SaaS eBook carousel for {product_name}.

    Composition:
    - Background: bold solid color block (use {primary_color})
    - Large centered headline: "Скачать бесплатно"
    - Below headline: small supporting text "{cover_title}"
    - Bottom-center: stylized button mockup labeled "Скачать"
    - Small "{product_name}" wordmark in corner
    - Subtle geometric pattern in background (dots / lines, low opacity)

    Style:
    - High-contrast, clearly call-to-action
    - Same visual family as the cover card (recognizable as the last card of the same carousel)
    - Bold typography

    Aspect ratio: 4:5 portrait.

    DO NOT include:
    - People or faces
    - Realistic photographs
    - Real button styling from VK / Facebook / other platforms
""").strip()


# ============ UI MOCKUP (PLACEHOLDER) ============

UI_MOCKUP_PLACEHOLDER = dedent("""
    [PLACEHOLDER — requires real product UI screenshot, AI generation is unreliable here]

    REQUIRED FROM CLIENT:
    - Real screenshot of {product_name} interface showing "{ui_concept}"
    - Resolution: at least 1080×1080 (preferable 2160×2160 for retina)
    - Format: PNG with transparent or solid background

    OPTIONAL FALLBACK — generate via dedicated mockup service:
    - Use Figma + Figma Plugin API (not OpenAI)
    - Or hire freelancer for clean UI mockup
    - Or use stock screenshots from {product_name} press kit

    If AI generation is forced (low quality expected):
    -----
    Clean B2B SaaS UI mockup screenshot of a project management tool.
    Show: kanban board with columns "{ui_concept}", several task cards with project names.
    Brand colors: primary {primary_color}, accent {accent_color}.
    Top navigation bar with product name "{product_name}".
    Sidebar with workspace navigation.
    Style: realistic UI screenshot, similar to Asana / Notion / Linear.
    Aspect ratio: 1:1 square.
    -----
""").strip()


# ============ TYPE DETECTION ============

def detect_creative_type(creative: dict) -> str:
    """
    Определяет какой шаблон использовать для креатива на основе его name/format/notes.
    Возвращает один из: 'ebook_cover', 'pain_split', 'abstract_brand',
    'carousel_card', 'ui_mockup'.
    """
    name = (creative.get("name", "") or "").lower()
    fmt = (creative.get("format", "") or "").lower()
    image_desc = (creative.get("image_or_video", "") or "").lower()

    # Видео — не наш модуль (это M2)
    if "video" in fmt or "video" in image_desc:
        return "video_skip"

    # eBook карусель (cover + preview + cta)
    if "ebook" in name or "cover" in image_desc or "lead_form" in fmt:
        return "ebook_cover"

    # Pain reframe split-screen
    if "pain" in name or "split-screen" in image_desc or "split" in image_desc:
        return "pain_split"

    # Карусель скриншотов UI (no-code сценарий с UI шагами)
    if "nocode" in name or ("carousel" in fmt and "ui" in image_desc.lower()):
        return "ui_mockup"

    # Обычная карусель — карточки
    if "carousel" in fmt:
        return "carousel_card"

    # UI скриншот (teams marketing, etc)
    if "ui" in image_desc or "screenshot" in image_desc:
        return "ui_mockup"

    # Дефолт
    return "abstract_brand"


# ============ BUILDER ============

def build_prompt(
    creative: dict,
    brand: dict,
    creative_type: str = None,
    card_index: int = 1,
    card_total: int = 1,
) -> str:
    """
    Собирает готовый prompt-string для OpenAI Images API.

    Args:
        creative: словарь креатива из creatives.json
        brand: словарь brand config (primary_color, accent_color, product_name, voice)
        creative_type: явно указанный тип (опционально, иначе detect)
        card_index, card_total: для карусели — индекс карточки

    Returns:
        промпт-строка для OpenAI Images API
    """
    if creative_type is None:
        creative_type = detect_creative_type(creative)

    if creative_type == "video_skip":
        return "[SKIP — video creative, use M2 generator instead]"

    product_name = brand.get("product_name", "Product")
    primary_color = brand.get("primary_color", "#2D5BFF")
    accent_color = brand.get("accent_color", "#22C55E")

    title = creative.get("title", "")
    description = creative.get("description", "")

    if creative_type == "ebook_cover":
        # Достаём краткое summary из description как visual_concept
        first_sentence = description.split("?")[0].split(".")[0][:100]
        visual_concept = first_sentence or "team collaboration and workflow"

        # Для eBook карусели — разные карточки разных типов:
        # card 1 = cover, последняя card = CTA, middle cards = preview pages
        if card_total > 1:
            if card_index == 1:
                template = EBOOK_COVER
            elif card_index == card_total:
                template = EBOOK_CTA_CARD
            else:
                template = EBOOK_PREVIEW_PAGE
        else:
            template = EBOOK_COVER

        return template.format(
            cover_title=title,
            primary_color=primary_color,
            accent_color=accent_color,
            product_name=product_name,
            visual_concept=visual_concept,
        )

    if creative_type == "pain_split":
        return PAIN_SPLIT.format(
            product_name=product_name,
            primary_color=primary_color,
            accent_color=accent_color,
            pain_headline=title,
        )

    if creative_type == "carousel_card":
        # Берём первое предложение description как card_body
        first = description.split("\n")[0].split(".")[0][:80]
        return CAROUSEL_CARD.format(
            index=card_index,
            total=card_total,
            card_title=title,
            concept=first,
            card_body=first,
            product_name=product_name,
            previous_index=max(1, card_index - 1),
            next_index=min(card_total, card_index + 1),
        )

    if creative_type == "ui_mockup":
        return UI_MOCKUP_PLACEHOLDER.format(
            product_name=product_name,
            primary_color=primary_color,
            accent_color=accent_color,
            ui_concept=title,
        )

    # Default: abstract_brand
    return ABSTRACT_BRAND.format(
        product_name=product_name,
        primary_color=primary_color,
        accent_color=accent_color,
        main_text=title,
    )


# ============ SIZE MAPPING ============

# OpenAI Images API supported sizes vs VK Ads target sizes
OPENAI_SIZE_FOR_TYPE = {
    "ebook_cover": "1024x1536",      # 2:3 portrait
    "pain_split": "1024x1536",       # 2:3 portrait → resize to 1080x1350
    "abstract_brand": "1024x1024",   # 1:1 square → resize to 1080x1080
    "carousel_card": "1024x1024",    # 1:1 → 1080x1080
    "ui_mockup": "1024x1024",        # 1:1 → 1080x1080
}

VK_TARGET_SIZE_FOR_TYPE = {
    "ebook_cover": (1080, 1350),     # 4:5 portrait
    "pain_split": (1080, 1350),      # 4:5
    "abstract_brand": (1080, 1080),  # 1:1
    "carousel_card": (1080, 1080),   # 1:1
    "ui_mockup": (1080, 1080),       # 1:1
}


def get_sizes(creative_type: str):
    """Возвращает (openai_size, vk_target_size) для данного типа."""
    return (
        OPENAI_SIZE_FOR_TYPE.get(creative_type, "1024x1024"),
        VK_TARGET_SIZE_FOR_TYPE.get(creative_type, (1080, 1080)),
    )
