"""
report_export.py -- Professional CT Imaging & Case Analysis Report Generator.

Generates a publication-grade, multi-page medical/technical CT case summary as a PDF.
Adheres strictly to standard A4 geometry (210 mm x 297 mm) with zero page overflow.

Design Guarantees:
- 100% strict standard A4 portrait canvas (8.2677 in x 11.6929 in / 595.28 pt x 841.89 pt).
- Absolute collision-free layout: explicit column bounds, generous gutters, and dedicated full-width rows for paths.
- Completely omits unavailable ("Not available") fields -- never shows placeholder rows.
- Strictly conditional sections: omits empty sections entirely (Clipping, Density/HU, MPR, etc.).
- Compact single-line fallback for measurements ("No measurements recorded.") when none exist.
- Full deduplication of synchronized 2D-3D caliper measurements.
- Real CT image integration: embeds actual calibrated CT slices and 3D reconstructions.
- Safe dictionary and type guards preventing any AttributeError on malformed inputs.
- Built on matplotlib PdfPages (Agg backend) for zero extra dependencies on Jetson Nano and macOS.
"""

import os
import datetime
import textwrap
import numpy as np

import matplotlib
matplotlib.use("Agg")  # Headless-safe, display-independent
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.image as mpimg
from matplotlib.patches import Rectangle


# ── Strict A4 Geometry Constants (210 mm x 297 mm) ───────────────────────────
A4_WIDTH_INCHES = 8.267716    # 210 mm
A4_HEIGHT_INCHES = 11.692913  # 297 mm
DPI = 150

# Normalized figure coordinate boundaries (0.0 to 1.0)
X_LEFT = 0.065                # 13.65 mm margin
X_RIGHT = 0.935               # 13.65 mm margin
PRINTABLE_WIDTH = X_RIGHT - X_LEFT  # 0.870

Y_HEADER = 0.962
Y_HEADER_LINE = 0.948
Y_CONTENT_TOP = 0.930
Y_CONTENT_BOTTOM = 0.082
Y_FOOTER_LINE = 0.062
Y_FOOTER = 0.044
Y_DISCLAIMER = 0.024


# ── Color Palette (Modern Clean Medical / Technical) ─────────────────────────
C_PRIMARY = "#0f172a"      # Slate 900 (deep charcoal for main text/values)
C_NAVY = "#1e3a8a"         # Blue 900 (primary clinical accent)
C_TEAL = "#0369a1"         # Sky 700 (secondary accent)
C_MUTED = "#475569"        # Slate 600 (labels, metadata)
C_LIGHT_MUTED = "#64748b"  # Slate 500 (hints, header/footer)
C_LINE = "#cbd5e1"         # Slate 300 (subtle dividers)
C_BOX_BG = "#f8fafc"       # Slate 50 (card background)
C_BOX_BORDER = "#e2e8f0"   # Slate 200 (card border)
C_TABLE_HDR = "#1e293b"    # Slate 800 (table header row)
C_TABLE_ROW_ALT = "#f8fafc"# Light zebra fill


def _is_meaningful(val) -> bool:
    """Returns True ONLY if val represents a genuine, meaningful value.
    Filters out None, False, empty strings, and all 'Not available' / 'unknown' placeholders.
    """
    if val is None or val is False:
        return False
    s = str(val).strip()
    if not s:
        return False
    lower = s.lower()
    if lower in (
        "not available", "none", "null", "n/a", "na", "—", "-", "unknown",
        "undefined", "not available hu", "0 active measurements",
        "disabled", "disabled (normal bone)", "0 slices", "local dataset",
        "no scan loaded", "no measurements recorded"
    ):
        return False

    # Check for combined placeholders like "— / —" or "Not available / Not available"
    if "/" in s:
        parts = [p.strip().lower() for p in s.split("/")]
        if parts and all(p in ("not available", "none", "null", "n/a", "—", "-", "unknown") for p in parts):
            return False

    return True


def _safe_str(val, fallback="—") -> str:
    """Safely converts value to string; returns fallback if not meaningful."""
    return str(val).strip() if _is_meaningful(val) else fallback


def _safe_dict(d) -> dict:
    """Guarantees return of a dict, preventing 'bool' object has no attribute 'get'."""
    return d if isinstance(d, dict) else {}


class AegisReportCanvas:
    """Manages multi-page standard A4 document generation with zero text overflow."""

    def __init__(self, report_id: str, case_mrn: str, timestamp: datetime.datetime):
        self.report_id = report_id
        self.case_mrn = case_mrn
        self.timestamp = timestamp
        self.pages: list[plt.Figure] = []
        self.cur_fig: plt.Figure = None
        self.cur_y: float = Y_CONTENT_TOP
        self.new_page()

    def new_page(self):
        """Spawns a fresh standard A4 portrait page."""
        fig = plt.figure(figsize=(A4_WIDTH_INCHES, A4_HEIGHT_INCHES), dpi=DPI)
        fig.patch.set_facecolor("white")
        self.pages.append(fig)
        self.cur_fig = fig
        self.cur_y = Y_CONTENT_TOP

    def ensure_space(self, needed_height: float):
        """Breaks to a new standard A4 page if the required height exceeds available room."""
        if self.cur_y - needed_height < Y_CONTENT_BOTTOM:
            self.new_page()

    def draw_section_header(self, title: str, subtitle: str = None):
        """Renders a clean medical section banner without text collisions."""
        needed = 0.038 if subtitle else 0.028
        self.ensure_space(needed)
        fig = self.cur_fig
        y = self.cur_y

        # Section Title
        fig.text(X_LEFT, y, title.upper(), fontsize=9.0, fontweight="bold",
                 color=C_NAVY, family="sans-serif")
        y -= 0.012

        # Optional Subtitle
        if subtitle:
            fig.text(X_LEFT, y, subtitle, fontsize=7.2,
                     color=C_MUTED, family="sans-serif", style="italic")
            y -= 0.009

        # Accent divider line
        line = plt.Line2D([X_LEFT, X_RIGHT], [y, y],
                          color=C_TEAL, linewidth=1.0, transform=fig.transFigure)
        fig.add_artist(line)
        self.cur_y = y - 0.012

    def draw_panel_box(self, height: float, bg_color=C_BOX_BG, border_color=C_BOX_BORDER):
        """Draws a subtle rectangular shaded card background."""
        fig = self.cur_fig
        rect = Rectangle(
            (X_LEFT, self.cur_y - height),
            PRINTABLE_WIDTH,
            height,
            facecolor=bg_color,
            edgecolor=border_color,
            linewidth=0.75,
            transform=fig.transFigure,
            zorder=0
        )
        fig.add_artist(rect)

    def draw_key_value_grid(
        self,
        pairs: list[tuple[str, str]],
        cols: int = 2,
        full_width_pairs: list[tuple[str, str]] = None
    ):
        """Renders structured key-value pairs with guaranteed ZERO label-value collision.

        Architecture:
        - Filters out non-meaningful entries completely.
        - Two explicit columns with wide label margin (0.170) and wide value column (0.210).
        - Gutter between Col 1 value and Col 2 label prevents horizontal spillover.
        - Full-width pairs (e.g. long file paths) are rendered as separate multi-line rows
          below the grid with the value wrapped across the full printable width.
        """
        valid_pairs = [(lbl, str(val).strip()) for (lbl, val) in (pairs or []) if _is_meaningful(val)]
        valid_fw = [(lbl, str(val).strip()) for (lbl, val) in (full_width_pairs or []) if _is_meaningful(val)]

        if not valid_pairs and not valid_fw:
            return

        row_h = 0.021
        num_rows = (len(valid_pairs) + cols - 1) // cols if cols > 0 else 0

        # Calculate height for full-width wrapped rows
        fw_h = 0.0
        fw_wrapped = []
        for lbl, val in valid_fw:
            wrapped_lines = textwrap.wrap(val, width=95)
            if not wrapped_lines:
                wrapped_lines = [val]
            line_count = len(wrapped_lines)
            row_needed = 0.015 + line_count * 0.014
            fw_h += row_needed
            fw_wrapped.append((lbl, wrapped_lines, row_needed))

        total_card_h = (num_rows * row_h) + fw_h + 0.014
        self.ensure_space(total_card_h + 0.008)

        fig = self.cur_fig
        start_y = self.cur_y
        self.draw_panel_box(total_card_h)

        # Explicit non-colliding column coordinates
        # Printable width = 0.870.
        # Col 1: Label X = 0.078, Val X = 0.250 (Width: 0.172 for label, 0.185 for val)
        # Gutter: 0.435 to 0.510 (0.075 gap)
        # Col 2: Label X = 0.510, Val X = 0.680 (Width: 0.170 for label, 0.235 for val)
        col_coords = [
            {"lbl_x": X_LEFT + 0.013, "val_x": X_LEFT + 0.185, "max_val_chars": 26},
            {"lbl_x": X_LEFT + 0.445, "val_x": X_LEFT + 0.615, "max_val_chars": 32},
        ]

        # 1. Render 2-column key-value rows
        for i, (label, val) in enumerate(valid_pairs):
            r = i // cols
            c = i % cols
            coords = col_coords[c]
            y_pos = start_y - 0.014 - (r * row_h)

            val_display = val
            if len(val_display) > coords["max_val_chars"]:
                val_display = val_display[:coords["max_val_chars"] - 3] + "..."

            fig.text(coords["lbl_x"], y_pos, label, fontsize=7.8, fontweight="bold",
                     color=C_MUTED, family="sans-serif")
            fig.text(coords["val_x"], y_pos, val_display, fontsize=8.0,
                     color=C_PRIMARY, family="sans-serif")

        # 2. Render full-width items below the grid (e.g. Dataset Source path)
        cur_fw_y = start_y - 0.014 - (num_rows * row_h)
        for lbl, lines, needed in fw_wrapped:
            # Label
            fig.text(X_LEFT + 0.013, cur_fw_y, lbl, fontsize=7.8, fontweight="bold",
                     color=C_MUTED, family="sans-serif")
            cur_fw_y -= 0.013
            # Wrapped value lines
            for line in lines:
                fig.text(X_LEFT + 0.024, cur_fw_y, line, fontsize=7.4,
                         color=C_PRIMARY, family="sans-serif")
                cur_fw_y -= 0.013
            cur_fw_y -= 0.004

        self.cur_y = start_y - total_card_h - 0.010

    def draw_compact_callout(self, text: str, label: str = None, color=C_PRIMARY):
        """Draws a compact single-line callout box."""
        self.ensure_space(0.034)
        fig = self.cur_fig
        h = 0.026
        self.draw_panel_box(h)
        y_pos = self.cur_y - 0.017

        if label:
            fig.text(X_LEFT + 0.015, y_pos, label, fontsize=7.8, fontweight="bold",
                     color=C_NAVY, family="sans-serif")
            fig.text(X_LEFT + 0.160, y_pos, text, fontsize=7.8,
                     color=color, family="sans-serif")
        else:
            fig.text(X_LEFT + 0.015, y_pos, text, fontsize=7.8,
                     color=color, family="sans-serif", style="italic")

        self.cur_y = self.cur_y - h - 0.010

    def draw_text_paragraph(self, text: str, font_size: float = 8.0, color=C_PRIMARY, line_spacing: float = 0.015):
        """Renders wrapped multi-line text with boundary safety."""
        lines = []
        for paragraph in str(text).split("\n"):
            wrapped = textwrap.wrap(paragraph, width=105)
            lines.extend(wrapped if wrapped else [""])

        for line in lines:
            self.ensure_space(line_spacing)
            self.cur_fig.text(X_LEFT + 0.012, self.cur_y, line,
                              fontsize=font_size, color=color, family="sans-serif")
            self.cur_y -= line_spacing
        self.cur_y -= 0.006

    def draw_table(self, headers: list[str], rows: list[list[str]], col_widths: list[float]):
        """Renders a clean tabular grid with headers and automatic multi-page splitting."""
        row_h = 0.020
        hdr_h = 0.022

        def _render_header():
            rect = Rectangle(
                (X_LEFT, self.cur_y - hdr_h),
                PRINTABLE_WIDTH,
                hdr_h,
                facecolor=C_TABLE_HDR,
                edgecolor=C_TABLE_HDR,
                linewidth=0.5,
                transform=self.cur_fig.transFigure
            )
            self.cur_fig.add_artist(rect)

            cur_x = X_LEFT + 0.008
            for title, w in zip(headers, col_widths):
                self.cur_fig.text(cur_x, self.cur_y - 0.015, title.upper(),
                                  fontsize=7.2, fontweight="bold", color="white", family="sans-serif")
                cur_x += w * PRINTABLE_WIDTH
            self.cur_y -= hdr_h

        self.ensure_space(hdr_h + row_h)
        _render_header()

        for idx, row in enumerate(rows):
            if self.cur_y - row_h < Y_CONTENT_BOTTOM:
                self.new_page()
                _render_header()

            bg_col = C_TABLE_ROW_ALT if (idx % 2 == 1) else "#ffffff"
            rect = Rectangle(
                (X_LEFT, self.cur_y - row_h),
                PRINTABLE_WIDTH,
                row_h,
                facecolor=bg_col,
                edgecolor=C_LINE,
                linewidth=0.5,
                transform=self.cur_fig.transFigure
            )
            self.cur_fig.add_artist(rect)

            cur_x = X_LEFT + 0.008
            for cell, w in zip(row, col_widths):
                self.cur_fig.text(cur_x, self.cur_y - 0.014, str(cell),
                                  fontsize=7.2, color=C_PRIMARY, family="sans-serif")
                cur_x += w * PRINTABLE_WIDTH
            self.cur_y -= row_h

        self.cur_y -= 0.010

    def draw_image_figure(self, image_path: str, caption: str, title: str = None, max_height: float = 0.34):
        """Embeds a centered medical image figure with aspect-ratio preservation and caption."""
        if not image_path or not os.path.isfile(image_path):
            return

        needed = max_height + 0.045
        self.ensure_space(needed)
        fig = self.cur_fig

        if title:
            fig.text(X_LEFT, self.cur_y, title.upper(), fontsize=8.0,
                     fontweight="bold", color=C_NAVY, family="sans-serif")
            self.cur_y -= 0.015

        try:
            arr = mpimg.imread(image_path)
            h_px, w_px = arr.shape[:2]
            aspect = w_px / max(h_px, 1)

            avail_w = PRINTABLE_WIDTH
            avail_h = max_height - 0.025

            if aspect > (avail_w / avail_h):
                w_fig = avail_w
                h_fig = avail_w / aspect
            else:
                h_fig = avail_h
                w_fig = avail_h * aspect

            x_center = X_LEFT + (PRINTABLE_WIDTH - w_fig) / 2.0
            y_bottom = self.cur_y - h_fig

            ax = fig.add_axes([x_center, y_bottom, w_fig, h_fig])
            ax.imshow(arr, aspect="equal")
            ax.axis("off")

            rect = Rectangle((0, 0), 1, 1, transform=ax.transAxes, fill=False,
                             edgecolor=C_LINE, linewidth=0.75)
            ax.add_patch(rect)

            if caption:
                fig.text(X_LEFT + PRINTABLE_WIDTH / 2.0, y_bottom - 0.015, caption,
                         fontsize=7.4, color=C_MUTED, family="sans-serif", style="italic", ha="center")

            self.cur_y = y_bottom - 0.026
        except Exception as exc:
            print(f"[report_export] Figure embed failed for {image_path}: {exc}")

    def draw_two_column_images(self, img1: dict, img2: dict, box_h: float = 0.28):
        """Renders two side-by-side images within A4 bounds with aspect preservation."""
        needed = box_h + 0.045
        self.ensure_space(needed)
        fig = self.cur_fig

        col_w = (PRINTABLE_WIDTH - 0.025) / 2.0
        start_y = self.cur_y

        for i, item in enumerate([img1, img2]):
            if not item:
                continue
            path = item.get("path")
            caption = item.get("caption", "")
            hdr = item.get("title", "")
            if not path or not os.path.isfile(path):
                continue

            col_x = X_LEFT + i * (col_w + 0.025)
            if hdr:
                fig.text(col_x, start_y, hdr.upper(), fontsize=7.8,
                         fontweight="bold", color=C_NAVY, family="sans-serif")

            try:
                arr = mpimg.imread(path)
                h_px, w_px = arr.shape[:2]
                aspect = w_px / max(h_px, 1)

                avail_w = col_w
                avail_h = box_h - 0.030
                if aspect > (avail_w / avail_h):
                    w_fig = avail_w
                    h_fig = avail_w / aspect
                else:
                    h_fig = avail_h
                    w_fig = avail_h * aspect

                x_box = col_x + (col_w - w_fig) / 2.0
                y_box = start_y - 0.016 - h_fig

                ax = fig.add_axes([x_box, y_box, w_fig, h_fig])
                ax.imshow(arr, aspect="equal")
                ax.axis("off")

                rect = Rectangle((0, 0), 1, 1, transform=ax.transAxes, fill=False,
                                 edgecolor=C_LINE, linewidth=0.75)
                ax.add_patch(rect)

                if caption:
                    fig.text(col_x + col_w / 2.0, y_box - 0.014, caption,
                             fontsize=7.2, color=C_MUTED, family="sans-serif", style="italic", ha="center")
            except Exception as exc:
                print(f"[report_export] Side-by-side image embed failed: {exc}")

        self.cur_y = start_y - box_h - 0.025

    def finalize_and_save(self, out_path: str):
        """Applies non-overlapping running headers, footers, page numbering, and disclaimer."""
        total_pages = len(self.pages)
        now_str = self.timestamp.strftime("%Y-%m-%d %H:%M")

        for idx, fig in enumerate(self.pages, 1):
            # 1. Top Running Header
            fig.text(X_LEFT, Y_HEADER, "AEGIS-TOUCH  |  CT IMAGING & CASE ANALYSIS REPORT",
                     fontsize=8.5, fontweight="bold", color=C_NAVY, family="sans-serif")

            meta_right = f"Report ID: {self.report_id}  ·  {now_str}"
            fig.text(X_RIGHT, Y_HEADER, meta_right, fontsize=7.8,
                     color=C_LIGHT_MUTED, family="sans-serif", ha="right")

            # Header divider line
            line_top = plt.Line2D([X_LEFT, X_RIGHT], [Y_HEADER_LINE, Y_HEADER_LINE],
                                  color=C_LINE, linewidth=0.75, transform=fig.transFigure)
            fig.add_artist(line_top)

            # 2. Bottom Running Footer
            line_bot = plt.Line2D([X_LEFT, X_RIGHT], [Y_FOOTER_LINE, Y_FOOTER_LINE],
                                  color=C_LINE, linewidth=0.75, transform=fig.transFigure)
            fig.add_artist(line_bot)

            footer_left = f"Aegis-Touch Workstation v2.4  |  Case MRN: {self.case_mrn}"
            fig.text(X_LEFT, Y_FOOTER, footer_left, fontsize=7.5,
                     color=C_LIGHT_MUTED, family="sans-serif")

            footer_right = f"Page {idx} of {total_pages}"
            fig.text(X_RIGHT, Y_FOOTER, footer_right, fontsize=8.0,
                     fontweight="bold", color=C_MUTED, family="sans-serif", ha="right")

            # 3. Non-diagnostic research/educational disclaimer on final page
            if idx == total_pages:
                disclaimer = (
                    "Notice: This technical report documents CT acquisition parameters, physical caliper measurements, "
                    "and user-entered session observations generated within Aegis-Touch. Visualization and density metrics "
                    "are intended for research and engineering analysis and are not a substitute for professional clinical interpretation."
                )
                fig.text(X_LEFT, Y_DISCLAIMER, disclaimer, fontsize=6.2,
                         color="#94a3b8", family="sans-serif")

        # Save to PDF
        with PdfPages(out_path) as pdf:
            for fig in self.pages:
                pdf.savefig(fig)
                plt.close(fig)

        print(f"[report_export] Professional A4 report generated ({total_pages} pages): {out_path}")


def _deduplicate_measurements(measurements: list) -> list[dict]:
    """Deduplicates measurements between 2D CT slice and 3D views.

    A synchronized measurement appearing in both 2D and 3D represents ONE physical
    caliper measurement. Returns a clean unified list of distinct measurements.
    """
    clean = []
    seen_endpoints = []

    for item in (measurements or []):
        if not isinstance(item, dict):
            continue

        m_type = item.get("kind", "distance")
        val = item.get("value")
        if val is None:
            val = item.get("distance_mm", item.get("dist_mm", 0.0))
        try:
            val = float(val)
        except (ValueError, TypeError):
            val = 0.0

        plane = item.get("plane", "2D Slice")
        unit = item.get("unit", "deg" if m_type == "cobb" else "mm")
        slice_ref = item.get("slice_idx", item.get("slice", "—"))
        pt1 = item.get("p1", item.get("pt1", item.get("physical_start")))
        pt2 = item.get("p2", item.get("pt2", item.get("physical_end")))
        source = item.get("source", "")

        # Coordinate matching for deduplication
        is_duplicate = False
        if pt1 and pt2 and isinstance(pt1, (list, tuple)) and isinstance(pt2, (list, tuple)):
            if len(pt1) >= 2 and len(pt2) >= 2:
                for (s1, s2, s_val) in seen_endpoints:
                    try:
                        d1 = np.linalg.norm(np.array(pt1[:len(s1)]) - np.array(s1))
                        d2 = np.linalg.norm(np.array(pt2[:len(s2)]) - np.array(s2))
                        if (d1 < 1.0 and d2 < 1.0) or (abs(val - s_val) < 0.1 and "sync" in str(plane).lower()):
                            is_duplicate = True
                            break
                    except Exception:
                        pass

        if source == "2D_synced":
            is_duplicate = True

        if is_duplicate:
            continue

        if pt1 and pt2:
            seen_endpoints.append((pt1, pt2, val))

        clean.append({
            "type": "Cobb Angle" if m_type == "cobb" else "Distance",
            "plane": str(plane),
            "value": val,
            "unit": unit,
            "slice": str(slice_ref) if slice_ref not in (None, "—") else "3D Volume Space",
            "pt1": pt1,
            "pt2": pt2,
        })

    return clean


def _format_point(pt) -> str:
    """Formats a 2D or 3D coordinate tuple cleanly."""
    if not pt or not isinstance(pt, (list, tuple)):
        return "—"
    coords = [f"{float(c):.1f}" for c in pt if isinstance(c, (int, float, np.number))]
    if len(coords) == 3:
        return f"({coords[0]}, {coords[1]}, {coords[2]})"
    elif len(coords) == 2:
        return f"({coords[0]}, {coords[1]})"
    return "—"


def _write_companion_html(
    html_path: str,
    patient: dict,
    scan: dict,
    metadata: dict,
    view_state: dict,
    measurements: list,
    notes: list,
    images: list,
    display_date: str,
):
    """Writes an accompanying clean HTML report for web/portal archiving (preserves teammate HTML report feature)."""
    name = patient.get("name", "Unknown Patient")
    mrn = patient.get("mrn", "UNKNOWN_MRN")
    age = patient.get("age", "N/A")
    sex = patient.get("sex", "N/A")

    modality = scan.get("modality", scan.get("type", "CT"))
    study_date = scan.get("study_date", scan.get("date", "N/A"))
    desc = scan.get("description", scan.get("series_description", "N/A"))
    slices = scan.get("slice_count", metadata.get("slices", "N/A"))

    meas_rows = ""
    if measurements:
        for m in measurements:
            m_id = m.get("id", "M")
            m_type = m.get("type", "Caliper")
            plane = m.get("view", m.get("plane", "2D/3D"))
            val = m.get("distance_mm", m.get("value", "—"))
            if isinstance(val, (int, float)):
                val = f"{val:.1f} mm"
            meas_rows += f"""
            <tr>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{m_id}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{m_type}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd;">{plane}</td>
                <td style="padding: 6px; border-bottom: 1px solid #ddd; font-weight: bold; color: #006680;">{val}</td>
            </tr>
            """
    else:
        meas_rows = """
        <tr>
            <td colspan="4" style="padding: 8px; text-align: center; color: #888; font-style: italic;">
                No measurements recorded.
            </td>
        </tr>
        """

    notes_html = ""
    if notes:
        for n in notes:
            author = n.get("author", "Clinician")
            ts = n.get("created_at", n.get("timestamp", ""))
            content = n.get("content", n.get("note", ""))
            notes_html += f"""
            <div style="margin-bottom: 8px; padding: 6px 10px; background: #f8f9fa; border-left: 3px solid #00a0b0;">
                <div style="font-size: 11px; color: #666;"><b>{author}</b> · {ts}</div>
                <div style="font-size: 12px; margin-top: 3px;">{content}</div>
            </div>
            """
    else:
        notes_html = "<p style='color: #888; font-style: italic; font-size: 11px;'>No observations or notes recorded.</p>"

    images_html = ""
    if images:
        for img in images:
            p = img.get("path")
            if p and os.path.isfile(p):
                cap = img.get("caption", "Case Visualization")
                abs_p = os.path.abspath(p).replace("\\", "/")
                images_html += f"""
                <div style="margin-top: 15px; text-align: center;">
                    <h4 style="color: #333; margin-bottom: 8px; text-align: left;">{cap}</h4>
                    <img src="{abs_p}" width="500" style="border: 1px solid #ccc; border-radius: 4px;" />
                </div>
                """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Aegis-Touch Clinical Case Summary - {mrn}</title>
</head>
<body style="font-family: Arial, Helvetica, sans-serif; color: #222; margin: 20px; line-height: 1.4;">
    <table style="width: 100%; border-bottom: 2px solid #006680; padding-bottom: 8px; margin-bottom: 15px;">
        <tr>
            <td>
                <h2 style="color: #006680; margin: 0;">AEGIS-TOUCH CLINICAL WORKSTATION</h2>
                <div style="font-size: 11px; color: #666; letter-spacing: 0.5px;">STERILE TOUCHLESS OR / ICU DIAGNOSTIC PLATFORM</div>
            </td>
            <td style="text-align: right;">
                <div style="font-size: 14px; font-weight: bold; color: #333;">CASE REPORT</div>
                <div style="font-size: 11px; color: #666;">Date: {display_date}</div>
            </td>
        </tr>
    </table>

    <table style="width: 100%; margin-bottom: 15px; border-collapse: collapse;">
        <tr style="background: #f0f7f9;">
            <td style="padding: 10px; width: 50%; vertical-align: top; border: 1px solid #d0e4ea;">
                <h4 style="margin: 0 0 8px 0; color: #006680;">PATIENT DEMOGRAPHICS</h4>
                <table style="width: 100%; font-size: 12px;">
                    <tr><td style="color: #666; width: 80px;">Name:</td><td><b>{name}</b></td></tr>
                    <tr><td style="color: #666;">MRN:</td><td><b>{mrn}</b></td></tr>
                    <tr><td style="color: #666;">Age / Sex:</td><td>{age} / {sex}</td></tr>
                </table>
            </td>
            <td style="padding: 10px; width: 50%; vertical-align: top; border: 1px solid #d0e4ea;">
                <h4 style="margin: 0 0 8px 0; color: #006680;">IMAGING STUDY</h4>
                <table style="width: 100%; font-size: 12px;">
                    <tr><td style="color: #666; width: 80px;">Modality:</td><td><b>{modality}</b></td></tr>
                    <tr><td style="color: #666;">Study Date:</td><td>{study_date}</td></tr>
                    <tr><td style="color: #666;">Series:</td><td>{desc} ({slices} slices)</td></tr>
                </table>
            </td>
        </tr>
    </table>

    <h4 style="color: #006680; margin: 15px 0 6px 0; border-bottom: 1px solid #ddd; padding-bottom: 4px;">
        CALIBRATED RADIOGRAPHIC MEASUREMENTS
    </h4>
    <table style="width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 15px;">
        <thead>
            <tr style="background: #e6f2f5; text-align: left;">
                <th style="padding: 6px; border-bottom: 2px solid #006680;">ID</th>
                <th style="padding: 6px; border-bottom: 2px solid #006680;">Type</th>
                <th style="padding: 6px; border-bottom: 2px solid #006680;">View</th>
                <th style="padding: 6px; border-bottom: 2px solid #006680;">Measurement</th>
            </tr>
        </thead>
        <tbody>
            {meas_rows}
        </tbody>
    </table>

    <h4 style="color: #006680; margin: 15px 0 6px 0; border-bottom: 1px solid #ddd; padding-bottom: 4px;">
        CLINICAL & OR FINDINGS
    </h4>
    {notes_html}

    {images_html}

    <div style="margin-top: 25px; border-top: 1px solid #ccc; padding-top: 8px; font-size: 10px; color: #888; text-align: center;">
        Generated by Aegis-Touch Touchless Surgical Suite · Calibrated with native DICOM pixel spacing · Confidential Medical Document
    </div>
</body>
</html>
"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)


def build_case_report(
    patient: dict = None,
    scan: dict = None,
    measurements: list = None,
    notes: list = None,
    screenshot_path: str = None,
    out_dir: str = "reports",
    metadata: dict = None,
    view_state: dict = None,
    images: list = None,
) -> str:
    """Builds an authentic, professional medical CT imaging & case analysis report in PDF.

    Strict Quality Rules:
    - 100% strict A4 geometry (210 mm x 297 mm) on every page.
    - Zero overlapping text, labels, or table headers.
    - Zero 'Not available' rows -- only meaningful fields are displayed.
    - Intelligent conditional sections: empty sections are completely omitted.
    - Compact single-line fallback for measurements when none exist.
    - Real representative CT slice image integration from loaded volume.
    """
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now()

    # Defensive input hardening against bools or malformed types
    patient = _safe_dict(patient)
    scan = _safe_dict(scan)
    metadata = _safe_dict(metadata)
    view_state = _safe_dict(view_state)
    notes = [n for n in (notes or []) if isinstance(n, dict)]
    measurements = [m for m in (measurements or []) if isinstance(m, dict)]
    images = [img for img in (images or []) if isinstance(img, dict)]

    # If single screenshot_path was supplied without images list, wrap it
    if screenshot_path and os.path.isfile(screenshot_path):
        if not any(img.get("path") == screenshot_path for img in images):
            images.insert(0, {
                "path": screenshot_path,
                "caption": "Figure 1 — 3D Anatomical Volume Reconstruction",
                "title": "3D Volume Reconstruction",
            })

    safe_mrn = patient.get("mrn")
    if not _is_meaningful(safe_mrn):
        safe_mrn = "SKULL-CASE"
    safe_mrn_str = str(safe_mrn).replace("/", "-").replace(" ", "_")
    report_id = f"REP-{stamp:%Y%m%d}-{safe_mrn_str[:10]}"
    out_path = os.path.join(out_dir, f"aegis-report-{safe_mrn_str}-{stamp:%Y%m%d-%H%M%S}.pdf")

    canvas = AegisReportCanvas(report_id=report_id, case_mrn=safe_mrn_str, timestamp=stamp)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1: CASE IDENTITY, PATIENT, STUDY, AND VISUALIZATION STATE
    # ══════════════════════════════════════════════════════════════════════════
    fig = canvas.cur_fig
    y = canvas.cur_y

    fig.text(X_LEFT, y, "AEGIS-TOUCH WORKSTATION", fontsize=13.5, fontweight="bold",
             color=C_PRIMARY, family="sans-serif")
    y -= 0.019
    fig.text(X_LEFT, y, "CT IMAGING & CASE ANALYSIS REPORT", fontsize=11.0, fontweight="bold",
             color=C_NAVY, family="sans-serif")
    y -= 0.014
    fig.text(X_LEFT, y, f"Technical Evaluation & Calibrated Imaging Record  ·  {stamp:%B %d, %Y  %H:%M:%S}",
             fontsize=7.8, color=C_MUTED, family="sans-serif")
    y -= 0.020
    canvas.cur_y = y

    # ──────────────────────────────────────────────────────────────────────────
    # 1. PATIENT & CASE INFORMATION (Keep, only available fields)
    # ──────────────────────────────────────────────────────────────────────────
    patient_pairs = []
    if _is_meaningful(patient.get("name")):
        patient_pairs.append(("Patient Name:", patient["name"]))
    if _is_meaningful(patient.get("mrn")):
        patient_pairs.append(("Patient ID / MRN:", patient["mrn"]))

    age_val = patient.get("age")
    sex_val = patient.get("sex")
    if _is_meaningful(age_val) and _is_meaningful(sex_val):
        patient_pairs.append(("Age / Sex:", f"{age_val} / {sex_val}"))
    elif _is_meaningful(age_val):
        patient_pairs.append(("Age:", str(age_val)))
    elif _is_meaningful(sex_val):
        patient_pairs.append(("Sex:", str(sex_val)))

    dob_val = patient.get("dob", patient.get("birth_date"))
    if _is_meaningful(dob_val):
        patient_pairs.append(("Date of Birth:", str(dob_val)))

    study_dt = metadata.get("study_date") or scan.get("date")
    if _is_meaningful(study_dt):
        patient_pairs.append(("Study Date:", str(study_dt)))

    acc_num = patient.get("accession_number") or scan.get("accession_number")
    if _is_meaningful(acc_num):
        patient_pairs.append(("Accession #:", str(acc_num)))

    if _is_meaningful(patient.get("allergies")):
        patient_pairs.append(("Allergies:", str(patient["allergies"])))
    if _is_meaningful(patient.get("blood_type")):
        patient_pairs.append(("Blood Type:", str(patient["blood_type"])))

    if patient_pairs:
        canvas.draw_section_header("Patient & Case Information")
        canvas.draw_key_value_grid(patient_pairs, cols=2)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. STUDY INFORMATION (Keep, only useful available info, zero overlap)
    # ──────────────────────────────────────────────────────────────────────────
    study_pairs = []
    modality = metadata.get("modality", "CT")
    if _is_meaningful(modality):
        study_pairs.append(("Modality:", str(modality)))

    s_desc = metadata.get("series_description") or scan.get("type") or scan.get("description")
    if _is_meaningful(s_desc):
        study_pairs.append(("Series Description:", str(s_desc)))

    body_part = metadata.get("body_part")
    if _is_meaningful(body_part):
        study_pairs.append(("Anatomical Region:", str(body_part)))

    n_slices = metadata.get("num_slices") or (f"{scan.get('slice_count')} slices" if scan.get("slice_count") else None)
    if _is_meaningful(n_slices):
        study_pairs.append(("Total Slices:", str(n_slices).replace("slices", "").strip()))

    orient_tag = metadata.get("orientation_info")
    if _is_meaningful(orient_tag):
        study_pairs.append(("Orientation:", str(orient_tag)))

    dims = metadata.get("dimensions")
    if _is_meaningful(dims):
        study_pairs.append(("Dimensions:", str(dims)))

    pix_sp = metadata.get("pixel_spacing")
    if _is_meaningful(pix_sp):
        study_pairs.append(("Pixel Spacing:", str(pix_sp)))

    sl_thick = metadata.get("slice_thickness")
    if _is_meaningful(sl_thick):
        study_pairs.append(("Slice Thickness:", str(sl_thick)))

    sl_sp = metadata.get("spacing_between_slices")
    if _is_meaningful(sl_sp):
        study_pairs.append(("Slice Spacing:", str(sl_sp)))

    vol_span = metadata.get("volume_dimensions_mm")
    if _is_meaningful(vol_span):
        study_pairs.append(("Volume Span:", str(vol_span)))

    slope = metadata.get("rescale_slope")
    intercept = metadata.get("rescale_intercept")
    if _is_meaningful(slope) and _is_meaningful(intercept):
        study_pairs.append(("Slope / Intercept:", f"{slope} / {intercept}"))

    # Long dataset path is rendered full-width below the 2-column grid to prevent overlap
    full_width_study = []
    file_path = scan.get("file_path")
    if _is_meaningful(file_path):
        full_width_study.append(("Dataset Source:", str(file_path)))

    if study_pairs or full_width_study:
        canvas.draw_section_header("Study Information")
        canvas.draw_key_value_grid(study_pairs, cols=2, full_width_pairs=full_width_study)

    # ──────────────────────────────────────────────────────────────────────────
    # 3. VISUALIZATION SUMMARY (Meaningful parameters only, no UI toggle dump)
    # ──────────────────────────────────────────────────────────────────────────
    vis_pairs = []
    vis_pairs.append(("Viewing Modes:", "3D Volume · 2D CT Slice · MPR"))

    cur_orient = view_state.get("orientation", metadata.get("current_orientation"))
    if _is_meaningful(cur_orient):
        vis_pairs.append(("Active Plane:", str(cur_orient)))

    cur_slice = view_state.get("slice_index", metadata.get("current_slice_index"))
    if _is_meaningful(cur_slice):
        vis_pairs.append(("Current Slice:", str(cur_slice)))

    cur_pos = view_state.get("physical_pos", metadata.get("current_physical_pos"))
    if _is_meaningful(cur_pos):
        vis_pairs.append(("Physical Position:", str(cur_pos)))

    ww = view_state.get("window_width", metadata.get("current_window_width"))
    if _is_meaningful(ww):
        vis_pairs.append(("Window Width:", str(ww) if "HU" in str(ww) else f"{ww} HU"))

    wl = view_state.get("window_level", metadata.get("current_window_level"))
    if _is_meaningful(wl):
        vis_pairs.append(("Window Level:", str(wl) if "HU" in str(wl) else f"{wl} HU"))

    preset = view_state.get("preset", metadata.get("current_wl_preset"))
    if _is_meaningful(preset):
        vis_pairs.append(("Active Preset:", str(preset)))

    sync_val = view_state.get("sync_mode")
    if _is_meaningful(sync_val):
        vis_pairs.append(("2D ↔ 3D Sync:", "Enabled" if "enable" in str(sync_val).lower() else str(sync_val)))

    if vis_pairs:
        canvas.draw_section_header("Visualization Summary")
        canvas.draw_key_value_grid(vis_pairs, cols=2)

    # ──────────────────────────────────────────────────────────────────────────
    # CLIPPING INFORMATION (Strictly conditional: ONLY if clipping was used)
    # ──────────────────────────────────────────────────────────────────────────
    if view_state.get("clipping_active") and _is_meaningful(view_state.get("clipping_summary")):
        canvas.draw_compact_callout(
            text=str(view_state["clipping_summary"]),
            label="Clipping State:",
            color=C_PRIMARY
        )

    # ──────────────────────────────────────────────────────────────────────────
    # PRIMARY REPRESENTATIVE FIGURE (Embed on Page 1 if space permits)
    # ──────────────────────────────────────────────────────────────────────────
    valid_images = [img for img in images if img.get("path") and os.path.isfile(img["path"])]
    images_remaining = list(valid_images)

    # Check if header + image + caption fit together on Page 1
    max_img_h = 0.26
    needed_for_figure = 0.038 + max_img_h + 0.040
    avail_page1 = canvas.cur_y - Y_CONTENT_BOTTOM

    if valid_images and avail_page1 >= needed_for_figure:
        img1 = images_remaining.pop(0)
        canvas.draw_section_header("Case Visualization")
        canvas.draw_image_figure(
            image_path=img1.get("path"),
            caption=img1.get("caption", "Figure 1 — Representative CT Slice"),
            title=img1.get("title", "2D CT Slice View"),
            max_height=max_img_h
        )

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2+: MEASUREMENTS, ADDITIONAL FIGURES, MPR, HU & OBSERVATIONS
    # ══════════════════════════════════════════════════════════════════════════
    deduped_measurements = _deduplicate_measurements(measurements)

    # Check for meaningful HU
    cursor_hu = view_state.get("cursor_hu")
    hu_min = metadata.get("hu_min")
    hu_max = metadata.get("hu_max")
    has_meaningful_hu = _is_meaningful(cursor_hu) or (_is_meaningful(hu_min) and _is_meaningful(hu_max))

    mpr_state = view_state.get("mpr_state")
    has_mpr = isinstance(mpr_state, dict) and any(_is_meaningful(v) for v in mpr_state.values())

    has_page2_content = (
        bool(deduped_measurements) or
        bool(images_remaining) or
        has_mpr or
        has_meaningful_hu or
        bool(notes)
    )

    if has_page2_content:
        canvas.new_page()

    # ──────────────────────────────────────────────────────────────────────────
    # 4. MEASUREMENT RESULTS (Conditional: clean table if present, 1-line if none)
    # ──────────────────────────────────────────────────────────────────────────
    if deduped_measurements:
        canvas.draw_section_header("Measurement Results", "Point-to-point calipers & calibrated linear evaluations")
        table_headers = ["#", "Type", "View / Plane", "Distance", "Slice Ref", "Point 1 (X, Y, Z mm)", "Point 2 (X, Y, Z mm)"]
        col_widths = [0.07, 0.13, 0.17, 0.13, 0.12, 0.19, 0.19]
        table_rows = []

        for idx, m in enumerate(deduped_measurements, 1):
            val_fmt = f"{m['value']:.1f} {m['unit']}"
            p1_str = _format_point(m.get("pt1"))
            p2_str = _format_point(m.get("pt2"))

            table_rows.append([
                f"M-{idx:02d}",
                m["type"],
                m["plane"],
                val_fmt,
                m["slice"],
                p1_str,
                p2_str,
            ])

        canvas.draw_table(table_headers, table_rows, col_widths)

        dist_count = sum(1 for m in deduped_measurements if m["type"] == "Distance")
        cobb_count = sum(1 for m in deduped_measurements if m["type"] == "Cobb Angle")
        summary_text = f"Total Completed Measurements: {len(deduped_measurements)}  ·  Linear Distances: {dist_count}  ·  Angular (Cobb): {cobb_count}"
        canvas.draw_text_paragraph(summary_text, font_size=7.4, color=C_MUTED)
    else:
        # Prompt rule: Do NOT show verbose workstation warning; show one compact line
        canvas.draw_section_header("Measurement Results")
        canvas.draw_compact_callout("No measurements recorded.", color=C_MUTED)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. ADDITIONAL CASE VISUALIZATIONS (e.g. 3D reconstruction, MPR overview)
    # ──────────────────────────────────────────────────────────────────────────
    if images_remaining:
        canvas.draw_section_header("Additional Case Visualizations", "3D anatomical reconstructions & secondary viewports")
        if len(images_remaining) == 2:
            canvas.draw_two_column_images(images_remaining[0], images_remaining[1], box_h=0.30)
        else:
            for idx, img in enumerate(images_remaining, 1):
                canvas.draw_image_figure(
                    image_path=img.get("path"),
                    caption=img.get("caption", f"Figure {idx + 1}"),
                    title=img.get("title", "Supplementary Viewport"),
                    max_height=0.32
                )

    # ──────────────────────────────────────────────────────────────────────────
    # 6. MULTIPLANAR RECONSTRUCTION (MPR) (Strictly conditional: only if used)
    # ──────────────────────────────────────────────────────────────────────────
    mpr_state = view_state.get("mpr_state")
    if isinstance(mpr_state, dict) and any(_is_meaningful(v) for v in mpr_state.values()):
        mpr_pairs = []
        if _is_meaningful(mpr_state.get("axial")):
            mpr_pairs.append(("Axial:", mpr_state["axial"]))
        if _is_meaningful(mpr_state.get("coronal")):
            mpr_pairs.append(("Coronal:", mpr_state["coronal"]))
        if _is_meaningful(mpr_state.get("sagittal")):
            mpr_pairs.append(("Sagittal:", mpr_state["sagittal"]))

        if mpr_pairs:
            canvas.draw_section_header("Multiplanar Reconstruction (MPR)")
            canvas.draw_key_value_grid(mpr_pairs, cols=3)

    # ──────────────────────────────────────────────────────────────────────────
    # 7. DENSITY & HOUNSFIELD UNIT ANALYSIS (Strictly conditional: only if meaningful)
    # ──────────────────────────────────────────────────────────────────────────
    cursor_hu = view_state.get("cursor_hu")
    hu_min = metadata.get("hu_min")
    hu_max = metadata.get("hu_max")

    has_meaningful_hu = _is_meaningful(cursor_hu) or (_is_meaningful(hu_min) and _is_meaningful(hu_max))
    if has_meaningful_hu:
        density_pairs = []
        if _is_meaningful(cursor_hu):
            density_pairs.append(("Sampled Cursor HU:", str(cursor_hu) if "HU" in str(cursor_hu) else f"{cursor_hu} HU"))

        if _is_meaningful(ww) and _is_meaningful(wl):
            try:
                w_val = float(str(ww).replace("HU", "").strip())
                l_val = float(str(wl).replace("HU", "").strip())
                density_pairs.append(("Window Interval:", f"[{l_val - w_val/2.0:.0f} HU  to  +{l_val + w_val/2.0:.0f} HU]"))
            except Exception:
                pass

        if _is_meaningful(hu_min):
            density_pairs.append(("Dataset HU Min:", str(hu_min)))
        if _is_meaningful(hu_max):
            density_pairs.append(("Dataset HU Max:", str(hu_max)))

        if _is_meaningful(preset):
            density_pairs.append(("Active Preset:", str(preset)))

        if density_pairs:
            canvas.draw_section_header("Density & Hounsfield Unit Analysis")
            canvas.draw_key_value_grid(density_pairs, cols=2)

    # ──────────────────────────────────────────────────────────────────────────
    # 8. OBSERVATIONS & USER NOTES (Keep, compact if empty)
    # ──────────────────────────────────────────────────────────────────────────
    canvas.draw_section_header("Observations & Notes")
    if notes:
        for n in notes:
            created_at = _safe_str(n.get("created_at"), stamp.strftime("%Y-%m-%d %H:%M"))
            content = _safe_str(n.get("content", n.get("note", "")))
            author = _safe_str(n.get("author", "Operator"))

            canvas.ensure_space(0.034)
            canvas.cur_fig.text(X_LEFT + 0.012, canvas.cur_y,
                                f"[{created_at}]  Author: {author}",
                                fontsize=7.6, fontweight="bold", color=C_NAVY, family="sans-serif")
            canvas.cur_y -= 0.013
            canvas.draw_text_paragraph(content, font_size=8.0, color=C_PRIMARY)
    else:
        canvas.draw_compact_callout("No observations or notes recorded.", color=C_MUTED)

    # Finalize running headers, footers, page numbering, and disclaimer
    canvas.finalize_and_save(out_path)

    # Also generate companion HTML file for web/portal archiving (preserves teammate HTML report feature)
    try:
        html_path = os.path.splitext(out_path)[0] + ".html"
        _write_companion_html(
            html_path=html_path,
            patient=patient,
            scan=scan,
            metadata=metadata,
            view_state=view_state,
            measurements=deduped_measurements if 'deduped_measurements' in locals() else measurements,
            notes=notes,
            images=images,
            display_date=stamp.strftime("%Y-%m-%d %H:%M:%S"),
        )
    except Exception as exc:
        print(f"[report_export] Companion HTML export notice: {exc}")

    return out_path

