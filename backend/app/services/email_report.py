"""Display the complete continuous PDF as seamless, bounded-height email images."""
from __future__ import annotations

import base64
from contextlib import closing
from dataclasses import dataclass
from html import escape
from io import BytesIO
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class EmailReport:
    html: str
    images: dict[str, bytes]


def render_email_report(pdf_path: Path, output_path: Path) -> None:
    import pypdfium2 as pdfium

    images = {}
    rows = []
    with pdfium.PdfDocument(pdf_path) as document:
        page_count = len(document)
        if not page_count:
            raise ValueError("PDF 未包含可显示的页面")
        title = escape(document.get_metadata_value("Title") or "生产报表")
        for index in range(page_count):
            with closing(document[index]) as page:
                height = page.get_height()
                # Render strips directly to bound bitmap memory; zero spacing keeps one visual flow.
                for part in range(math.ceil(height / 2048)):
                    top, bottom = part * 2048, min(height, (part + 1) * 2048)
                    cid = f"report-page-{len(images) + 1}@cutting-report"
                    with (
                        closing(page.render(scale=2, crop=(0, height - bottom, 0, top))) as bitmap,
                        bitmap.to_pil() as image,
                        BytesIO() as content,
                    ):
                        image.save(content, format="PNG")
                        images[cid] = base64.b64encode(content.getvalue()).decode("ascii")
                    rows.append(
                        '<tr><td style="padding:0;border:0;font-size:0;line-height:0;">'
                        f'<img src="cid:{cid}" alt="{title}，连续内容 {len(images)}" '
                        'width="1120" style="display:block;width:100%;max-width:1120px;height:auto;border:0;margin:0;">'
                        '</td></tr>'
                    )
    html = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title></head>'
        '<body style="margin:0;padding:0;background:#ffffff;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%;max-width:1120px;margin:0 auto;border-collapse:collapse;">'
        f'<tbody>{"".join(rows)}</tbody></table></body></html>'
    )
    output_path.write_text(json.dumps({"html": html, "images": images}), encoding="utf-8")
