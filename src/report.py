"""PDF report generator — converts LLM markdown output to a styled PDF."""
import io
import os

import markdown
from xhtml2pdf import pisa

from config import REPORT_DIR

# ── Page & typography CSS (xhtml2pdf-compatible subset) ──────────────────────
_CSS = """
@page {
    size: A4;
    margin: 2.2cm 2.5cm 2.2cm 2.5cm;
}

body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.65;
    color: #1a1a2e;
}

h1 {
    font-size: 19pt;
    color: #0f3460;
    border-bottom: 2px solid #0f3460;
    padding-bottom: 6pt;
    margin-top: 0;
    margin-bottom: 14pt;
}

h2 {
    font-size: 13.5pt;
    color: #0f3460;
    border-bottom: 1px solid #c8cfe8;
    padding-bottom: 4pt;
    margin-top: 22pt;
    margin-bottom: 8pt;
}

h3 {
    font-size: 11pt;
    color: #16213e;
    margin-top: 14pt;
    margin-bottom: 4pt;
}

p {
    margin: 0 0 8pt 0;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 12pt 0 16pt 0;
    font-size: 9.5pt;
}

th {
    background-color: #0f3460;
    color: #ffffff;
    padding: 6pt 9pt;
    text-align: left;
    font-weight: bold;
}

td {
    padding: 5pt 9pt;
    border-bottom: 1px solid #dde2f0;
    vertical-align: top;
}

strong, b {
    color: #0f3460;
}

hr {
    border-top: 1px solid #dde2f0;
    margin: 18pt 0;
}

ul, ol {
    margin: 4pt 0 8pt 18pt;
    padding: 0;
}

li {
    margin-bottom: 3pt;
}

blockquote {
    border-left: 3pt solid #0f3460;
    margin: 10pt 0;
    padding: 5pt 14pt;
    color: #444;
    background: #f5f7fc;
}
"""

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def save_pdf(
    markdown_text: str,
    tickers: list[str],
    date_str: str,
    output_path: str | None = None,
) -> str:
    """Convert *markdown_text* to a styled PDF.

    Parameters
    ----------
    markdown_text : str
        Full markdown string produced by the LLM.
    tickers : list[str]
        Ticker symbols included in the report (used for the auto filename).
    date_str : str
        Date label (e.g. ``"April 08, 2026"``), used for the auto filename.
    output_path : str | None
        If provided, the PDF is saved to this exact path (directory is created
        if it does not exist). Otherwise the file is placed in REPORT_DIR with
        an auto-generated name.

    Returns
    -------
    str
        Absolute path of the saved PDF file.
    """
    if output_path:
        out_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    else:
        os.makedirs(REPORT_DIR, exist_ok=True)
        safe_date = date_str.replace(",", "").replace(" ", "_")
        if tickers:
            joined = "_".join(tickers)
            safe_tickers = f"{len(tickers)}tickers" if len(joined) > 80 else joined
        else:
            safe_tickers = "report"
        out_path = os.path.join(REPORT_DIR, f"{safe_date}_{safe_tickers}.pdf")

    # Markdown → HTML
    md_processor = markdown.Markdown(
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        output_format="html",
    )
    body_html = md_processor.convert(markdown_text)
    full_html = _HTML_TEMPLATE.format(css=_CSS, body=body_html)

    # HTML → PDF
    with open(out_path, "wb") as fh:
        result = pisa.CreatePDF(io.StringIO(full_html), dest=fh)
    if result.err:
        raise RuntimeError(f"xhtml2pdf conversion failed ({result.err} errors)")

    return os.path.abspath(out_path)
