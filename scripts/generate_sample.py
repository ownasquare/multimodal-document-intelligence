"""Generate the redistribution-safe Northstar multimodal PDF fixture.

The document is fictional. ReportLab's invariant mode and repository-owned Pillow
drawings make repeated generation byte-stable with the locked dependency set.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 54
INK = colors.HexColor("#17242A")
MUTED = colors.HexColor("#5E6A70")
TEAL = colors.HexColor("#12756D")
PALE_TEAL = colors.HexColor("#E8F3F1")
AMBER = colors.HexColor("#D89020")
OFF_WHITE = colors.HexColor("#F7F5F0")
GRID = colors.HexColor("#CCD5D7")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Return Pillow's bundled deterministic font without external font files."""

    return ImageFont.load_default(size=size)


def _png_reader(image: Image.Image) -> ImageReader:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    buffer.seek(0)
    return ImageReader(buffer)


def _draw_header(pdf: canvas.Canvas, page_number: int, section: str) -> None:
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 32, "NORTHSTAR OPERATIONS | Q2 2026")
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 32, section.upper())
    pdf.setStrokeColor(GRID)
    pdf.line(MARGIN, PAGE_HEIGHT - 42, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 42)
    pdf.setFillColor(MUTED)
    pdf.drawCentredString(
        PAGE_WIDTH / 2, 24, f"FICTIONAL TRAINING DOCUMENT | PAGE {page_number} OF 8"
    )


def _draw_title(pdf: canvas.Canvas, title: str, subtitle: str | None = None) -> None:
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 23)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 84, title)
    if subtitle:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(MARGIN, PAGE_HEIGHT - 104, subtitle)


def _draw_wrapped(
    pdf: canvas.Canvas,
    text: str,
    *,
    x: float,
    y: float,
    width_chars: int = 88,
    size: float = 10,
    leading: float = 15,
    color: colors.Color = INK,
    font: str = "Helvetica",
) -> float:
    pdf.setFillColor(color)
    pdf.setFont(font, size)
    cursor = y
    for line in wrap(text, width=width_chars, break_long_words=False):
        pdf.drawString(x, cursor, line)
        cursor -= leading
    return cursor


def _draw_metric_card(
    pdf: canvas.Canvas, *, x: float, y: float, width: float, label: str, value: str, note: str
) -> None:
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(GRID)
    pdf.roundRect(x, y, width, 88, 7, fill=1, stroke=1)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(x + 12, y + 66, label.upper())
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(x + 12, y + 37, value)
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(x + 12, y + 16, note)


def _monthly_chart() -> Image.Image:
    image = Image.new("RGB", (1200, 650), "#FBFAF7")
    draw = ImageDraw.Draw(image)
    title = _font(34, bold=True)
    body = _font(27)
    small = _font(22)
    draw.text((55, 30), "Monthly net revenue ($M)", fill="#17242A", font=title)
    chart_left, chart_top, chart_bottom, chart_right = 100, 110, 540, 1140
    draw.line((chart_left, chart_top, chart_left, chart_bottom), fill="#68767C", width=3)
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill="#68767C", width=3)
    for tick in range(5):
        y = chart_bottom - tick * 105
        draw.line((chart_left, y, chart_right, y), fill="#D7DDDE", width=2)
        draw.text((30, y - 14), f"{tick}", fill="#5E6A70", font=small)
    values = (("APR", 2.5), ("MAY", 2.7), ("JUN", 3.2))
    bar_width = 190
    for index, (month, value) in enumerate(values):
        x0 = 190 + index * 315
        x1 = x0 + bar_width
        y0 = chart_bottom - int(value * 105)
        draw.rounded_rectangle((x0, y0, x1, chart_bottom), radius=20, fill="#12756D")
        label = f"${value:.1f}M"
        draw.text((x0 + 35, y0 - 42), label, fill="#17242A", font=body)
        draw.text(
            (x0 + 18, chart_bottom + 24),
            f"{month}  {label}",
            fill="#17242A",
            font=body,
        )
    draw.text(
        (100, 610),
        "Labels are part of the raster chart.",
        fill="#5E6A70",
        font=small,
    )
    return image


def _product_mix_chart() -> Image.Image:
    image = Image.new("RGB", (1200, 650), "#FBFAF7")
    draw = ImageDraw.Draw(image)
    title = _font(34, bold=True)
    body = _font(27)
    small = _font(22)
    draw.text((55, 30), "Q2 gross bookings product mix", fill="#17242A", font=title)
    items = (
        ("CORE PLATFORM", 45, "#12756D"),
        ("PRO ANALYTICS", 35, "#4B9D92"),
        ("SERVICES", 20, "#D89020"),
    )
    y = 140
    for label, share, color in items:
        draw.text((70, y + 12), f"{label}  {share}%", fill="#17242A", font=body)
        draw.rounded_rectangle((430, y, 1080, y + 65), radius=18, fill="#E1E7E7")
        extent = 430 + int(650 * share / 50)
        draw.rounded_rectangle((430, y, extent, y + 65), radius=18, fill=color)
        y += 125
    draw.text((70, 545), "Gross bookings represented: $10.5M", fill="#17242A", font=body)
    draw.text((70, 600), "Percent labels are part of the raster chart.", fill="#5E6A70", font=small)
    return image


def _scanned_memo() -> Image.Image:
    image = Image.new("RGB", (1275, 1650), "#EEEDE8")
    draw = ImageDraw.Draw(image)
    for index in range(0, 1275, 17):
        y = (index * 37) % 1650
        shade = 214 + (index % 19)
        draw.point((index, y), fill=(shade, shade, shade))
        draw.point(((index * 7) % 1275, (y + 613) % 1650), fill=(shade, shade, shade))
    heading = _font(47, bold=True)
    subheading = _font(30, bold=True)
    body = _font(28)
    small = _font(23)
    draw.rectangle((90, 82, 1185, 1560), fill="#F8F7F2", outline="#B4B2AA", width=3)
    draw.text((145, 135), "NORTHSTAR OPERATIONS", fill="#252525", font=heading)
    draw.text((145, 205), "SCANNED INCIDENT MEMO", fill="#3E3E3E", font=subheading)
    draw.line((145, 260, 1120, 260), fill="#77756F", width=3)
    memo_lines = [
        "DATE: JUNE 15, 2026",
        "SUBJECT: JUNE 14 PACKING SYSTEM INTERRUPTION",
        "",
        "At 14:08 PT, the packing barcode service stopped accepting",
        "new sessions after its service certificate expired. The outage",
        "lasted 47 minutes and delayed 320 customer orders.",
        "",
        "Operations rerouted priority orders to the Central facility.",
        "Ninety-one percent of delayed orders cleared within 24 hours.",
        "No customer data was exposed and no orders were lost.",
        "",
        "ROOT CAUSE: Expired barcode-service certificate.",
        "CORRECTIVE ACTION: Add a 45-day certificate expiry alert and",
        "a weekly validation check owned by Platform Operations.",
        "",
        "Prepared by: M. Chen, Incident Lead",
    ]
    y = 340
    for line in memo_lines:
        current_font = subheading if line.startswith(("DATE:", "SUBJECT:", "ROOT CAUSE:")) else body
        draw.text((145, y), line, fill="#2A2A2A", font=current_font)
        y += 61 if line else 38
    draw.text(
        (145, 1490),
        "FICTIONAL TRAINING DOCUMENT | SCANNED COPY | PAGE 5",
        fill="#66645E",
        font=small,
    )
    return image.rotate(0.15, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="#E9E8E3")


def _flow_diagram() -> Image.Image:
    image = Image.new("RGB", (1200, 700), "#FBFAF7")
    draw = ImageDraw.Draw(image)
    title = _font(32, bold=True)
    body = _font(24, bold=True)
    small = _font(20)
    draw.text((45, 24), "Carrier capacity pressure-test", fill="#17242A", font=title)

    def box(x: int, y: int, w: int, h: int, label: str, *, color: str = "#E8F3F1") -> None:
        draw.rounded_rectangle(
            (x, y, x + w, y + h), radius=16, fill=color, outline="#557074", width=3
        )
        lines = wrap(label, width=18)
        text_y = y + 18
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=body)
            text_w = bbox[2] - bbox[0]
            draw.text((x + (w - text_w) / 2, text_y), line, fill="#17242A", font=body)
            text_y += 31

    def arrow(start: tuple[int, int], end: tuple[int, int], *, color: str = "#3D5A5E") -> None:
        draw.line((*start, *end), fill=color, width=6)
        ex, ey = end
        if abs(end[0] - start[0]) > abs(end[1] - start[1]):
            direction = 1 if end[0] > start[0] else -1
            points = [(ex, ey), (ex - 18 * direction, ey - 12), (ex - 18 * direction, ey + 12)]
        else:
            direction = 1 if end[1] > start[1] else -1
            points = [(ex, ey), (ex - 12, ey - 18 * direction), (ex + 12, ey - 18 * direction)]
        draw.polygon(points, fill=color)

    box(40, 145, 210, 95, "Demand alert")
    box(345, 145, 235, 95, "Regional reroute")
    box(680, 125, 240, 135, "Partner capacity available?")
    box(970, 65, 190, 95, "Release to partner")
    box(680, 390, 240, 100, "Manual hold", color="#FFF1D7")
    box(970, 390, 190, 100, "Incident lead approval", color="#FFF1D7")
    arrow((250, 192), (345, 192))
    arrow((580, 192), (680, 192))
    arrow((920, 155), (970, 112))
    arrow((800, 260), (800, 390), color="#B06A12")
    arrow((920, 440), (970, 440), color="#B06A12")
    draw.text((930, 112), "YES", fill="#12756D", font=small)
    draw.text((817, 315), "NO", fill="#B06A12", font=small)
    draw.text((680, 560), "Exception branch requires both amber steps.", fill="#5E6A70", font=small)
    return image


def _page_one(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 1, "Executive summary")
    _draw_title(
        pdf, "Q2 Operations Review", "Decision brief for the fictional Northstar leadership team"
    )
    pdf.setFillColor(OFF_WHITE)
    pdf.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT - 122, fill=1, stroke=0)
    _draw_metric_card(
        pdf, x=54, y=528, width=154, label="Net revenue", value="$8.4M", note="6% above plan"
    )
    _draw_metric_card(
        pdf, x=229, y=528, width=154, label="On-time dispatch", value="94.2%", note="+1.4 pts vs Q1"
    )
    _draw_metric_card(
        pdf, x=404, y=528, width=154, label="Orders", value="65,900", note="+8% vs Q1"
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(MARGIN, 485, "What changed")
    cursor = _draw_wrapped(
        pdf,
        "Net revenue reached $8.4 million, 6% above the operating plan. The West region "
        "combined the strongest on-time dispatch rate with the lowest return rate.",
        x=MARGIN,
        y=462,
        width_chars=92,
    )
    cursor -= 12
    cursor = _draw_wrapped(
        pdf,
        "June closed the quarter with the strongest monthly revenue. A packing-system "
        "interruption created a short-lived backlog, but most affected orders cleared "
        "within one day.",
        x=MARGIN,
        y=cursor,
        width_chars=92,
    )
    pdf.setFillColor(PALE_TEAL)
    pdf.roundRect(MARGIN, 225, PAGE_WIDTH - 2 * MARGIN, 120, 7, fill=1, stroke=0)
    pdf.setFillColor(TEAL)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(MARGIN + 16, 316, "DECISION FOCUS")
    _draw_wrapped(
        pdf,
        "Approve the carrier-capacity reserve before the August demand window, and keep "
        "the new certificate-expiry control in the weekly operations review.",
        x=MARGIN + 16,
        y=290,
        width_chars=82,
        size=11,
        leading=17,
    )
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(
        MARGIN,
        185,
        "Reporting basis: net revenue unless a visual or note explicitly says gross bookings.",
    )
    pdf.showPage()


def _page_two(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 2, "Regional performance")
    _draw_title(pdf, "Regional Performance", "Native-text operating table with quarter totals")
    data = [
        ["Region", "Net revenue\n($M)", "Orders", "On-time\n(%)", "Return\n(%)"],
        ["Northeast", "2.4", "18,400", "95.1", "2.2"],
        ["Southeast", "1.8", "14,700", "92.8", "2.7"],
        ["Central", "1.9", "15,200", "93.6", "2.4"],
        ["West", "2.3", "17,600", "95.4", "1.9"],
        ["Total", "8.4", "65,900", "94.2", "2.3"],
    ]
    table = Table(data, colWidths=[115, 105, 95, 95, 95], rowHeights=[42, 36, 36, 36, 36, 38])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), PALE_TEAL),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (1, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.75, GRID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, OFF_WHITE]),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    table.wrapOn(pdf, PAGE_WIDTH, PAGE_HEIGHT)
    table.drawOn(pdf, MARGIN, 385)
    _draw_wrapped(
        pdf,
        "Table note: Total on-time and return rates are order-weighted. Values are rounded "
        "to one decimal place.",
        x=MARGIN,
        y=360,
        width_chars=92,
        size=8.5,
        leading=12,
        color=MUTED,
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(MARGIN, 305, "Operating readout")
    _draw_wrapped(
        pdf,
        "West led on service quality at 95.4% on-time dispatch and a 1.9% return rate. "
        "Northeast remained the largest regional revenue contributor at $2.4 million.",
        x=MARGIN,
        y=280,
        width_chars=91,
    )
    pdf.showPage()


def _page_three(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 3, "Monthly revenue")
    _draw_title(pdf, "Monthly Revenue", "Raster chart - read the labels on the bars")
    pdf.drawImage(
        _png_reader(_monthly_chart()), MARGIN, 245, width=PAGE_WIDTH - 2 * MARGIN, height=300
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(MARGIN, 205, "Quarter pattern")
    _draw_wrapped(
        pdf,
        "Revenue increased in each month and June was the strongest close. Exact monthly "
        "values are available only in the chart labels above.",
        x=MARGIN,
        y=182,
        width_chars=92,
    )
    pdf.showPage()


def _page_four(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 4, "Product mix")
    _draw_title(pdf, "Product Mix", "Raster chart on a gross-bookings basis")
    pdf.drawImage(
        _png_reader(_product_mix_chart()), MARGIN, 250, width=PAGE_WIDTH - 2 * MARGIN, height=295
    )
    pdf.setFillColor(colors.HexColor("#FFF1D7"))
    pdf.roundRect(MARGIN, 135, PAGE_WIDTH - 2 * MARGIN, 80, 7, fill=1, stroke=0)
    pdf.setFillColor(colors.HexColor("#7A4C0D"))
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(MARGIN + 14, 193, "BASIS CAUTION")
    _draw_wrapped(
        pdf,
        "Gross bookings are measured before cancellations and refunds. The $10.5M total "
        "and its product shares are not directly comparable to Q2 net revenue.",
        x=MARGIN + 14,
        y=174,
        width_chars=84,
        size=9,
        leading=13,
        color=colors.HexColor("#5D3B0A"),
    )
    pdf.showPage()


def _page_five(pdf: canvas.Canvas) -> None:
    pdf.drawImage(_png_reader(_scanned_memo()), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)
    pdf.showPage()


def _page_six(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 6, "Pressure test")
    _draw_title(
        pdf,
        "Capacity Pressure Test",
        "Follow the raster flow diagram, including the exception branch",
    )
    pdf.drawImage(
        _png_reader(_flow_diagram()), MARGIN, 215, width=PAGE_WIDTH - 2 * MARGIN, height=345
    )
    _draw_wrapped(
        pdf,
        "Control note: green represents the standard route; amber represents the controlled "
        "exception route. Sequence labels are contained in the diagram.",
        x=MARGIN,
        y=175,
        width_chars=92,
        color=MUTED,
        size=9,
        leading=13,
    )
    pdf.showPage()


def _page_seven(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 7, "Risk register")
    _draw_title(pdf, "Risk Register", "Native-text table of open operational risks")
    data = [
        ["ID", "Risk", "Likelihood", "Impact", "Owner", "Mitigation"],
        ["R-01", "Forecast volatility", "Medium", "High", "Finance", "Weekly forecast refresh"],
        ["R-02", "Barcode certificate", "Low", "High", "Platform", "45-day expiry alert"],
        [
            "R-03",
            "Carrier capacity",
            "High",
            "High",
            "Logistics",
            "Reserve 12% partner capacity by Aug 15",
        ],
        ["R-04", "Return-rate drift", "Medium", "Medium", "CX", "New reason-code audit"],
    ]
    table = Table(data, colWidths=[42, 104, 67, 55, 62, 175], rowHeights=[36, 47, 47, 56, 47])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#FFF1D7")),
                ("GRID", (0, 0), (-1, -1), 0.75, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    table.wrapOn(pdf, PAGE_WIDTH, PAGE_HEIGHT)
    table.drawOn(pdf, MARGIN, 385)
    _draw_wrapped(
        pdf,
        "Priority rule: a High likelihood plus High impact rating requires a dated mitigation "
        "and weekly review until reduced.",
        x=MARGIN,
        y=350,
        width_chars=92,
        size=9,
        leading=13,
        color=MUTED,
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(MARGIN, 290, "Leadership action")
    _draw_wrapped(
        pdf,
        "Carrier capacity is the only open risk rated High for both likelihood and impact. "
        "Logistics owns the dated mitigation.",
        x=MARGIN,
        y=266,
        width_chars=91,
    )
    pdf.showPage()


def _page_eight(pdf: canvas.Canvas) -> None:
    _draw_header(pdf, 8, "Appendix")
    _draw_title(
        pdf, "Appendix: Measurement Basis", "Definitions for responsible cross-page comparison"
    )
    sections = (
        (
            "NET REVENUE",
            "Recognized revenue after cancellations, refunds, discounts, and transaction "
            "taxes. The Q2 total in this review is $8.4M.",
        ),
        (
            "GROSS BOOKINGS",
            "Booked order or contract value before cancellations and refunds. The product-mix "
            "visual uses $10.5M of gross bookings.",
        ),
        (
            "COMPARISON RULE",
            "Do not subtract gross bookings from net revenue and label the difference as "
            "leakage, margin, or lost revenue without a reconciliation by accounting basis.",
        ),
        (
            "OPERATING RATES",
            "On-time dispatch and return rates are order-based operational measures. They "
            "are not percentages of revenue.",
        ),
    )
    y = 625
    for label, body in sections:
        pdf.setFillColor(PALE_TEAL if label != "COMPARISON RULE" else colors.HexColor("#FFF1D7"))
        pdf.roundRect(MARGIN, y - 90, PAGE_WIDTH - 2 * MARGIN, 102, 7, fill=1, stroke=0)
        pdf.setFillColor(TEAL if label != "COMPARISON RULE" else colors.HexColor("#7A4C0D"))
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(MARGIN + 14, y - 10, label)
        _draw_wrapped(
            pdf,
            body,
            x=MARGIN + 14,
            y=y - 34,
            width_chars=84,
            size=9.5,
            leading=14,
            color=INK,
        )
        y -= 125
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(
        MARGIN,
        100,
        "Source map: pages 1-3 net revenue | page 4 gross bookings | page 7 operating risk "
        "ratings.",
    )
    pdf.showPage()


def build_sample(output_path: Path) -> Path:
    """Build the eight-page fixture at *output_path* and return its path."""

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(
        str(output_path),
        pagesize=letter,
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle("Northstar Q2 Operations Review")
    pdf.setAuthor("Document Intelligence Contributors")
    pdf.setSubject("Fictional multimodal document intelligence fixture")
    pdf.setCreator("Repository-owned deterministic sample generator")
    for page_builder in (
        _page_one,
        _page_two,
        _page_three,
        _page_four,
        _page_five,
        _page_six,
        _page_seven,
        _page_eight,
    ):
        page_builder(pdf)
    pdf.save()
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/northstar-q2-operations-review.pdf"),
        help="Destination PDF path",
    )
    args = parser.parse_args()
    result = build_sample(args.output)
    print(result)


if __name__ == "__main__":
    main()
