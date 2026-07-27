"""
video_prompt_templates.py — шаблоны промптов для видео-генерации.

3 типа сценариев:
- pain_reframe — split-screen «до/после»
- ugc_testimonial — реальный человек говорит на камеру (AI-имитация)
- product_demo — скринкаст или анимация UI

Все шаблоны рассчитаны на 5-10 секунд (лимит Runway / Kling).
Если в creatives.json указан сценарий > 10 сек — разбиваем на сегменты и склеиваем (TODO).
"""

from textwrap import dedent


# ============ PAIN REFRAME (5 сек MVP) ============

PAIN_REFRAME = dedent("""
    Vertical 9:16 social media ad video, 5 seconds.

    Scene description:
    - Opening shot (0-2s): chaotic desktop scene — multiple browser tabs open with conflicting notifications, scattered sticky notes, an Excel spreadsheet with red error highlights, a deadline reminder popup. Muted color palette (beige, grey, low saturation). Camera slowly pushes in to convey stress.
    - Transition (2-3s): smooth wipe or fade to right side, suggesting "switching tools"
    - Closing shot (3-5s): clean, organized {product_name} interface — orderly kanban board with neat task columns in brand color {primary_color}. Single focused screen, calm satisfied feeling. Soft lighting, vibrant color.

    Style: cinematic, professional B2B SaaS aesthetic. No people visible.
    Mood: tension → relief.
    No text overlay (will be added in post-production).
    Aspect ratio: 9:16 (vertical, for VK Реклама + VK Клипы).
""").strip()


# ============ UGC TESTIMONIAL (5 сек MVP) ============

UGC_TESTIMONIAL = dedent("""
    Vertical 9:16 social media ad video, 5 seconds.

    Scene description:
    - Real person (mid-30s, casual home-office attire) sitting at a desk with a laptop
    - Background: blurred home office or cafe, soft natural lighting
    - Action: person turns to camera with relaxed confident expression, gestures briefly while explaining (lip-sync not critical — silent video with later voiceover)
    - Camera: slight handheld feel, single static angle, slight depth of field
    - Color tone: warm, natural skin tones, not over-stylized

    Style: documentary-realism, authentic UGC aesthetic (like founder vlogs on LinkedIn / Twitter).
    Mood: friendly authority, "I've been there, here's what worked."
    No on-screen text (will be added in post-production).
    NO branded items visible.
    Aspect ratio: 9:16.

    Context for character (not visible — informs facial expression):
    "{ugc_context}"
""").strip()


# ============ PRODUCT DEMO (5 сек MVP) ============

PRODUCT_DEMO = dedent("""
    Vertical 9:16 social media ad video, 5 seconds.

    Scene description:
    - Screen recording-style shot of a clean B2B SaaS interface
    - Action: cursor enters frame, clicks on a kanban card, smoothly drags it across columns "{demo_action}"
    - Cards animate with subtle bounce, status changes color from neutral to accent {accent_color}
    - Brand colors throughout: primary {primary_color}, accent {accent_color}
    - White/light grey background
    - Subtle UI sound design implied (not generated — soundless OK)

    Style: clean tech demo, polished but not over-produced (like Notion / Linear product demos on Twitter).
    Mood: capable, smooth, satisfying.
    No people. No on-screen text (text overlay added in post).
    Aspect ratio: 9:16.
""").strip()


# ============ TYPE DETECTION ============

def detect_video_type(creative: dict) -> str:
    """
    Определяет тип видео-промпта для креатива.
    Возвращает: 'pain_reframe', 'ugc_testimonial', 'product_demo', or 'unknown'.
    """
    name = (creative.get("name", "") or "").lower()
    title = (creative.get("title", "") or "").lower()
    image_desc = (creative.get("image_or_video", "") or "").lower()
    script = (creative.get("video_script", "") or "").lower()

    if "pain" in name or "chaos" in script or "before" in script and "after" in script:
        return "pain_reframe"

    if "ugc" in name or "real person" in image_desc or "real person" in script:
        return "ugc_testimonial"

    if "demo" in name or "screencast" in image_desc or "ui" in image_desc:
        return "product_demo"

    return "unknown"


def build_video_prompt(
    creative: dict,
    brand: dict,
    video_type: str = None,
) -> str:
    """
    Собирает video-prompt для провайдера.

    Args:
        creative: словарь креатива из creatives.json
        brand: brand config (primary_color, accent_color, product_name)
        video_type: если не указан — определяется автоматически

    Returns:
        промпт-строка
    """
    if video_type is None:
        video_type = detect_video_type(creative)

    product_name = brand.get("product_name", "Product")
    primary_color = brand.get("primary_color", "#1E40AF")
    accent_color = brand.get("accent_color", "#22C55E")

    title = creative.get("title", "")
    description = creative.get("description", "")

    if video_type == "pain_reframe":
        return PAIN_REFRAME.format(
            product_name=product_name,
            primary_color=primary_color,
        )

    if video_type == "ugc_testimonial":
        ugc_context = description[:200] or title
        return UGC_TESTIMONIAL.format(
            ugc_context=ugc_context,
        )

    if video_type == "product_demo":
        return PRODUCT_DEMO.format(
            product_name=product_name,
            primary_color=primary_color,
            accent_color=accent_color,
            demo_action=title[:80] or "task management workflow",
        )

    # unknown — fallback на pain_reframe
    return PAIN_REFRAME.format(
        product_name=product_name,
        primary_color=primary_color,
    )
