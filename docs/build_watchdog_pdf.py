#!/usr/bin/env python3
#
# build_watchdog_pdf.py renders the watchdog documentation to PDF.
# Copyright (C) 2026  TuDo Makerspace, Patrick Pedersen <ctx.xda@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Render watchdog.md to watchdog.pdf via Pandoc and XeLaTeX."""

import re
import subprocess
import sys
import textwrap
from pathlib import Path

TITLE = "Watchdog Daemon Implementation Overview"
SUBTITLE = "Host-side WiFi stick recovery service"
AUTHOR = "Codex"

HEADER_L = "WiFi Watchdog"
HEADER_R = "Implementation Overview"

LANG = "en"
MAINFONT = "TeX Gyre Pagella"
MONOFONT = "DejaVu Sans Mono"
HIGHLIGHT = "tango"


def preprocess(src: str) -> str:
    src = re.sub(r"^# .*\n\n", "", src, count=1)
    src = re.sub(
        r"# (Table of Contents|Contents|Inhaltsverzeichnis)( <!-- omit in toc -->)?.*?(?=\n# )",
        "",
        src,
        flags=re.DOTALL | re.IGNORECASE,
    )
    src = src.replace(" <!-- omit in toc -->", "")
    src = re.sub(r"(?=^# )", r"\\newpage\n\n", src, count=1, flags=re.MULTILINE)
    return src


def front_matter() -> str:
    return textwrap.dedent(
        f"""\
        ---
        title: "{TITLE}"
        subtitle: "{SUBTITLE}"
        author: "{AUTHOR}"
        date: "\\\\today"
        lang: {LANG}

        toc: true
        toc-depth: 3
        toc-title: "Table of Contents"

        numbersections: false

        colorlinks: true
        linkcolor: "BrickRed"
        urlcolor: "NavyBlue"
        citecolor: "BrickRed"

        geometry: "a4paper, margin=2.5cm, top=3cm, bottom=3cm"
        fontsize: 11pt
        linestretch: 1.25

        mainfont: "{MAINFONT}"
        monofont: "{MONOFONT}"
        monofontoptions: "Scale=0.82"

        header-includes: |
          \\usepackage{{fvextra}}
          \\DefineVerbatimEnvironment{{Highlighting}}{{Verbatim}}{{breaklines,breakanywhere,commandchars=\\\\\\{{\\}}}}

          \\usepackage{{fancyhdr}}
          \\pagestyle{{fancy}}
          \\fancyhf{{}}
          \\fancyhead[L]{{\\small {HEADER_L}}}
          \\fancyhead[R]{{\\small {HEADER_R}}}
          \\fancyfoot[C]{{\\thepage}}
          \\renewcommand{{\\headrulewidth}}{{0.4pt}}
        ---

        """
    )


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: build_watchdog_pdf.py <input.md> <output.pdf>")

    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    raw = src_path.read_text(encoding="utf-8")
    processed = front_matter() + preprocess(raw).lstrip()

    tmp = Path("/tmp") / f"{src_path.stem}_pandoc.md"
    tmp.write_text(processed, encoding="utf-8")

    preamble = Path(__file__).resolve().parent / "pdf-preamble.tex"

    subprocess.run(
        [
            "pandoc",
            str(tmp),
            "-o",
            str(out_path),
            "--pdf-engine=xelatex",
            f"--highlight-style={HIGHLIGHT}",
            f"--include-in-header={preamble}",
        ],
        check=True,
    )

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
