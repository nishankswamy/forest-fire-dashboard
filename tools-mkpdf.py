#!/usr/bin/env python3
"""
Build a printable PDF of BRINGUP.md.

The point of this document is that it sits on the bench next to the Pis while
you wire them, so it is designed for print: A4, generous margins, page numbers,
and the wiring diagram dropped in full-width at the stage where you need it.
"""

import re
import markdown
from weasyprint import HTML, CSS

REPO = '/sessions/vigilant-gifted-archimedes/mnt/forest-fire-dashboard'
OUT = '/sessions/vigilant-gifted-archimedes/mnt/outputs/BRINGUP.pdf'

md_text = open(f'{REPO}/BRINGUP.md').read()

# Drop the wiring diagram in where the wiring actually happens. WeasyPrint
# renders inline SVG properly, so no rasterising needed.
svg = open(f'{REPO}/pi/docs/wiring.svg').read()
svg = re.sub(r'<\?xml[^>]*\?>', '', svg)
svg = svg.replace('width="1060" height="986"', 'width="100%"', 1)

md_text = md_text.replace(
    'Only now. `pi/docs/wiring.svg` has the full pin map.',
    'Only now. The full pin map is below — the same diagram as `pi/docs/wiring.svg`.\n\n'
    '<div class="wiring">' + svg + '</div>\n')

html_body = markdown.markdown(
    md_text,
    extensions=['tables', 'fenced_code', 'sane_lists', 'attr_list', 'toc'])

# Checkbox list at the end — markdown leaves the literal brackets.
html_body = html_body.replace('[ ] ', '<span class="box"></span> ')

CSS_TEXT = """
@page {
  size: A4;
  margin: 18mm 16mm 20mm 16mm;
  @bottom-center {
    content: "Forest Fire Detection — Hardware Bring-Up      page " counter(page) " of " counter(pages);
    font-family: Helvetica, Arial, sans-serif;
    font-size: 8pt; color: #6E7D71;
  }
}
@page :first { @bottom-center { content: ""; } }

body {
  font-family: Helvetica, Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.5; color: #1B2A20;
}

h1 {
  font-size: 24pt; color: #14301F; margin: 0 0 4pt;
  border-bottom: 2px solid #6FA07A; padding-bottom: 6pt;
}
h2 {
  font-size: 14pt; color: #14301F; margin: 20pt 0 7pt;
  page-break-after: avoid; break-after: avoid;
}
h2[id^="stage"] {
  background: #14301F; color: #fff;
  padding: 7pt 10pt; border-radius: 3pt; margin-top: 22pt;
}
h3 { font-size: 11pt; color: #1E4630; margin: 14pt 0 5pt;
     page-break-after: avoid; break-after: avoid; }

p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 16pt; }
li { margin-bottom: 3pt; }

/* keep a heading with the block that follows it */
h2 + p, h2 + table, h2 + pre, h3 + p, h3 + table, h3 + pre { break-before: avoid; }

code {
  font-family: "DejaVu Sans Mono", Courier, monospace;
  font-size: 8.6pt; background: #EDF2EE; color: #14301F;
  padding: 1pt 3pt; border-radius: 2pt;
}
pre {
  background: #14301F; color: #E8F0E9;
  padding: 8pt 10pt; border-radius: 4pt;
  font-size: 8.4pt; line-height: 1.45;
  overflow-wrap: break-word; white-space: pre-wrap;
  break-inside: avoid;
}
pre code { background: none; color: inherit; padding: 0; font-size: 8.4pt; }

table {
  width: 100%; border-collapse: collapse; margin: 6pt 0 10pt;
  font-size: 8.8pt; break-inside: avoid;
}
th {
  background: #1E4630; color: #fff; text-align: left;
  padding: 5pt 7pt; font-weight: bold;
}
td { padding: 5pt 7pt; border-bottom: 1px solid #DCE4DE; vertical-align: top; }
tr:nth-child(even) td { background: #F5F8F5; }

blockquote {
  margin: 8pt 0; padding: 7pt 11pt;
  background: #FDF3EE; border-left: 3px solid #E8622C;
  font-size: 9pt;
}
blockquote p { margin: 0 0 4pt; }
blockquote p:last-child { margin: 0; }

hr { border: none; border-top: 1px solid #DCE4DE; margin: 16pt 0; }

strong { color: #14301F; }

.wiring {
  break-before: page; break-inside: avoid;
  margin: 8pt 0 12pt; text-align: center;
}
.wiring svg { width: 100%; height: auto; }

.box {
  display: inline-block; width: 9pt; height: 9pt;
  border: 1.2pt solid #6E7D71; border-radius: 1.5pt;
  margin-right: 5pt; vertical-align: -0.5pt;
}
"""

full = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Hardware Bring-Up</title></head>
<body>{html_body}</body></html>"""

HTML(string=full, base_url=REPO).write_pdf(OUT, stylesheets=[CSS(string=CSS_TEXT)])
print('written', OUT)
