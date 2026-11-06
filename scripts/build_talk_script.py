"""Build docs/talk/script.html, the talk's teleprompter page, from docs/talk/README.md.

The page opens from disk as an offline backup, and a browser will not fetch() the markdown from file://,
so the script is baked into the HTML here. The output depends only on the markdown and the template, so
tests/test_talk.py can compare it byte for byte. Run: python scripts/build_talk_script.py
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "docs" / "talk" / "README.md"
OUT = HERE.parent / "docs" / "talk" / "script.html"
TEMPLATE = HERE / "talk_script.template.html"

SPEAKERS = ("Daniel", "Karissa")
DIRECTION = re.compile(r"\[[^\]]*\]")
TOKEN = re.compile(r"<(https?://[^>\s]+)>|`([^`]+)`")
PLACEHOLDERS = ("HEADING", "LEDE", "RUN_OF_SHOW_NAV", "SLIDES_NOTE", "BEATS", "ANSWERS_NOTE",
                "PREPARED_ANSWERS", "QUIET_ROOM", "TIMEKEEPING", "BEFORE_THE_TALK")


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def inline(text: str, directions: bool = False) -> str:
    """Markdown inline to HTML: autolinks and code first, then escape the rest, then bold and directions."""
    text = " ".join(text.split())
    held: list[str] = []

    def hold(m: re.Match) -> str:
        if m.group(1):
            held.append(f'<a class="url" href="{esc(m.group(1))}">{esc(m.group(1))}</a>')
        else:
            held.append(f"<code>{esc(m.group(2))}</code>")
        return f"\x00{len(held) - 1}\x00"

    out = esc(TOKEN.sub(hold, text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    if directions:
        out = DIRECTION.sub(lambda m: f'<span class="sd">{m.group(0)}</span>', out)
    return re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], out)


def sections(markdown: str) -> dict[str, str]:
    parts = re.split(r"^## (.+)$", markdown, flags=re.M)
    return {parts[k].strip(): parts[k + 1] for k in range(1, len(parts), 2)}


def paragraphs(body: str) -> list[str]:
    return [p.strip() for p in body.split("\n\n") if p.strip()]


def blocks(body: str) -> list[tuple[str, object]]:
    """Split markdown into ("p", text), ("table", rows), ("ul" | "ol", items) and ("code", (lang, text))."""
    lines, out, k = body.split("\n"), [], 0
    while k < len(lines):
        line = lines[k]
        if line.startswith("```"):
            end = lines.index("```", k + 1)
            out.append(("code", (line[3:].strip(), "\n".join(lines[k + 1:end]))))
            k = end + 1
        elif not line.strip():
            k += 1
        elif line.startswith("|"):
            rows = []
            while k < len(lines) and lines[k].startswith("|"):
                cells = [c.strip() for c in lines[k].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                k += 1
            out.append(("table", rows))
        elif re.match(r"(- |\d+\. )", line):
            kind = "ul" if line.startswith("- ") else "ol"
            items = []
            while k < len(lines) and lines[k].strip():
                m = re.match(r"(?:- |\d+\. )(.*)$", lines[k])
                if m:
                    items.append(m.group(1))
                else:
                    items[-1] += " " + lines[k].strip()
                k += 1
            out.append((kind, items))
        else:
            text = []
            while k < len(lines) and lines[k].strip() and not re.match(r"(```|\||- |\d+\. )", lines[k]):
                text.append(lines[k])
                k += 1
            out.append(("p", " ".join(text)))
    return out


def blocks_html(items: list[tuple[str, object]]) -> str:
    html = []
    for kind, value in items:
        if kind == "p":
            html.append(f'<p class="prose">{inline(value)}</p>')
        elif kind in ("ul", "ol"):
            lis = "\n".join(f"<li>{inline(i)}</li>" for i in value)
            html.append(f'<{kind} class="list">\n{lis}\n</{kind}>')
        elif kind == "table":
            head = "".join(f'<th scope="col">{inline(c)}</th>' for c in value[0])
            body = "\n".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
                             for row in value[1:])
            html.append(f'<div class="table-wrap"><table>\n<thead><tr>{head}</tr></thead>\n'
                        f"<tbody>\n{body}\n</tbody>\n</table></div>")
        else:
            lang, text = value
            html.append(f'<pre class="code"><code data-lang="{esc(lang)}">{esc(text)}</code></pre>')
    return "\n".join(html)


def secs(t: str) -> int:
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def run_of_show(body: str) -> tuple[list[dict], list[tuple[str, object]]]:
    """The beats from the run-of-show table, and the whole section's blocks for the timekeeping: the table
    itself with every column, then the minute-by-minute table and the order of cuts."""
    items = blocks(body)
    at = next(k for k, (kind, _) in enumerate(items) if kind == "table")
    head, *rows = items[at][1]
    col = {name.lower(): k for k, name in enumerate(head)}
    beats = []
    for row in rows:
        clock = row[col["clock"]]
        m = re.fullmatch(r"(\d+:\d\d) to (\d+:\d\d)", clock)
        beats.append({
            "n": int(row[col["slide"]]),
            "clock": clock,
            "start": m.group(1) if m else None,
            "end": m.group(2) if m else None,
            "name": row[col["beat"]],
            "who": row[col["who"]],
            "room": row[col["the room is"]],
        })
    return beats, items


def slide_bodies(body: str) -> tuple[str, dict[int, dict]]:
    """The note before the first slide, and each slide's heading parts, screen text and speaker blocks."""
    parts = re.split(r"^### Slide (\d+) (.*)$", body, flags=re.M)
    slides = {}
    for k in range(1, len(parts), 3):
        heading = [p.strip() for p in parts[k + 1].split("·") if p.strip()]
        screen, speakers, current = "", [], None
        for para in paragraphs(parts[k + 2]):
            m = re.match(r"^\*\*(Daniel|Karissa|Notes|Screen):\*\*\s*(.*)$", para, re.S)
            if m and m.group(1) == "Screen":
                screen, current = m.group(2), None
            elif m:
                current = {"who": m.group(1), "paras": [m.group(2)] if m.group(2) else []}
                speakers.append(current)
            elif current is not None:
                current["paras"].append(para)
            else:
                raise ValueError(f"slide {parts[k]}: a paragraph with no speaker: {para[:60]}")
        slides[int(parts[k])] = {"heading": heading, "screen": screen, "speakers": speakers}
    return parts[0], slides


def names_in(who: str) -> list[str]:
    if who == "Both":
        return list(SPEAKERS)
    return [n for n in re.findall(r"[A-Z][a-z]+", who) if n in SPEAKERS]


def chips(who: str) -> str:
    if not who:
        return ""
    if who == "Both":
        return '<span class="chip" data-chip="Daniel">Daniel</span> and <span class="chip" data-chip="Karissa">Karissa</span>'
    out = esc(who)
    for name in SPEAKERS:
        out = out.replace(name, f'<span class="chip" data-chip="{name}">{name}</span>')
    return out


def clock_label(beat: dict) -> str:
    return esc(beat["clock"][:1].upper() + beat["clock"][1:])


def line(para: str) -> str:
    text = " ".join(para.split())
    if DIRECTION.fullmatch(text):
        return f'<p class="line sd">{esc(text)}</p>'
    return f'<p class="line">{inline(text, directions=True)}</p>'


def beat_html(beat: dict, slide: dict) -> str:
    n = beat["n"]
    if slide["heading"][0] != beat["name"]:
        raise ValueError(f"slide {n}: the heading names {slide['heading'][0]!r}, the run of show {beat['name']!r}")
    hands_on = "hands-on" in slide["heading"]
    times = f' data-start="{beat["start"]}" data-end="{beat["end"]}"' if beat["start"] else ""
    tag = ' <span class="tag-hands">Hands-on</span>' if hands_on else ""
    who = chips(beat["who"])
    who_html = f'<span class="who-list">{who}</span>' if who else ""
    out = [
        f'<section class="beat" data-slide="{n}" id="beat-{n}"{times} aria-labelledby="beat-{n}-name">',
        '<div class="gutter" aria-hidden="true"><span class="fill"></span></div>',
        '<header class="beat-head">',
        f'<p class="beat-meta t-meta"><span class="window">{clock_label(beat)}</span> <span class="slide-no">Slide {n}</span>{tag}</p>',
        f'<h2 class="beat-name t-beat" id="beat-{n}-name">{esc(beat["name"])}</h2>',
        f'<p class="beat-who t-meta">{who_html}<span class="room">The room is: {esc(beat["room"])}</span></p>',
    ]
    if slide["screen"]:
        out.append(f'<p class="screen t-small"><span class="screen-label">On screen</span> {inline(slide["screen"])}</p>')
    out.append("</header>")
    for block in slide["speakers"]:
        if block["who"] == "Notes":
            out.append('<div class="block notes">\n<p class="who t-label">Notes</p>')
            out += [f'<p class="line t-note">{inline(p)}</p>' for p in block["paras"]]
        else:
            out.append(f'<div class="block" data-who="{block["who"]}">\n<p class="who t-label">{block["who"]}</p>')
            out += [line(p) for p in block["paras"]]
        out.append("</div>")
    out.append("</section>")
    return "\n".join(out)


def nav_html(beats: list[dict], slides: dict[int, dict]) -> str:
    out = []
    for beat in beats:
        n, start, end = beat["n"], beat["start"], beat["end"]
        length = secs(end) - secs(start) if start else 0
        hands_on = "hands-on" in slides[n]["heading"]
        dots = "".join(f'<span class="dot" data-chip="{name}"></span>' for name in names_in(beat["who"]))
        who = f'<span class="vh">, {esc(beat["who"])}</span>' if beat["who"] else ""
        out.append(
            f'<li class="seg-item{" title" if not start else ""}{" hands-on" if hands_on else ""}" style="--secs:{length}">'
            f'<a class="seg" href="#beat-{n}" data-beat="{n}">'
            f'<span class="seg-track" aria-hidden="true"></span>'
            f'<span class="seg-time t-num">{clock_label(beat)}</span>'
            f'<span class="seg-dots" aria-hidden="true">{dots}</span>'
            f'<span class="seg-name">{esc(beat["name"])}{who}'
            + (' <span class="seg-tag">Hands-on</span>' if hands_on else "")
            + "</span></a></li>")
    return "\n".join(out)


def answers_html(body: str) -> tuple[str, str, str]:
    """The note above the answers, the ten answers, and the questions for a quiet room."""
    items = blocks(body)
    note = next(v for kind, v in items if kind == "p")
    answers = next(v for kind, v in items if kind == "ol")
    out = []
    for k, item in enumerate(answers, 1):
        m = re.fullmatch(r"\*\*(.+?)\*\*\s+\[(Daniel|Karissa)\]\s+(.*)", item, re.S)
        if not m:
            raise ValueError(f"prepared answer {k} has no question and [speaker]: {item[:60]}")
        question, who, answer = m.groups()
        out.append(
            f'<li class="answer" data-who="{who}" id="answer-{k}">\n<div class="q-row">'
            f'<p class="q t-question"><span class="q-num t-num">{k}</span> {inline(question)}</p>'
            f'<span class="chip" data-chip="{who}">{who}</span></div>\n'
            f'<p class="a t-answer">{inline(answer, directions=True)}</p>\n</li>')
    (kind, lead), *rest = items[[kind for kind, _ in items].index("ol") + 1:]
    if kind != "p":
        raise ValueError("the prepared answers should be followed by the questions for a quiet room")
    return inline(note), "\n".join(out), f'<h3>{inline(lead.rstrip(":"))}</h3>\n{blocks_html(rest)}'


def render(markdown: str) -> str:
    title = re.search(r"^# (.+)$", markdown, flags=re.M).group(1)
    intro = paragraphs(markdown.split("\n", 1)[1].split("\n## ")[0])[0]
    parts = sections(markdown)
    beats, timekeeping = run_of_show(parts["Run of show"])
    note, slides = slide_bodies(parts["Slides and speaker notes"])
    if [b["n"] for b in beats] != sorted(slides):
        raise ValueError("the run-of-show table and the ### Slide sections list different slides")
    answers_note, answers, quiet = answers_html(parts["Prepared answers"])
    fills = {
        "HEADING": esc(title),
        "LEDE": inline(intro),
        "RUN_OF_SHOW_NAV": nav_html(beats, slides),
        "SLIDES_NOTE": "\n".join(f'<p class="prose">{inline(p)}</p>' for p in paragraphs(note)),
        "BEATS": "\n\n".join(beat_html(b, slides[b["n"]]) for b in beats),
        "ANSWERS_NOTE": answers_note,
        "PREPARED_ANSWERS": answers,
        "QUIET_ROOM": quiet,
        "TIMEKEEPING": blocks_html(timekeeping),
        "BEFORE_THE_TALK": blocks_html(blocks(parts["Before the talk"])),
    }
    page = TEMPLATE.read_text(encoding="utf-8")
    for name in PLACEHOLDERS:
        key = "{{" + name + "}}"
        if page.count(key) != 1:
            raise ValueError(f"the template must hold {key} exactly once")
        page = page.replace(key, fills[name])
    return page


def main() -> None:
    OUT.write_text(render(SOURCE.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
