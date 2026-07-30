"""Side-effect-free, safe Markdown rendering shared by generators and APIs."""
import html
import re


def md_to_html(markdown):
    """Small Markdown -> HTML renderer: escape first, then add safe markup."""
    out, lines, i = [], html.escape(markdown or "").split("\n"), 0

    def inline(value):
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        value = re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<em>\2</em>", value)
        value = re.sub(
            r"\[([^\]]+)\]\((https?:[^)\s]+)\)",
            r'<a href="\2">\1</a>',
            value,
        )
        return value

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
        elif re.match(r"^#{1,3}\s", line):
            level = len(re.match(r"^#+", line).group())
            out.append(
                f"<h{level}>"
                + inline(re.sub(r"^#+\s*", "", line))
                + f"</h{level}>"
            )
            i += 1
        elif re.match(r"^(-{3,}|\*{3,})\s*$", line):
            out.append("<hr>")
            i += 1
        elif re.match(r"^\s*&gt;\s?", line):
            buf = []
            while i < len(lines) and re.match(r"^\s*&gt;\s?", lines[i]):
                buf.append(re.sub(r"^\s*&gt;\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + inline(" ".join(buf)) + "</blockquote>")
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            tag = "ol" if re.match(r"^\s*\d+\.", line) else "ul"
            items = []
            while i < len(lines) and re.match(
                    r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(
                    "<li>"
                    + inline(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]))
                    + "</li>"
                )
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
        elif ("|" in line and i + 1 < len(lines)
              and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1])):
            def row(value):
                return [
                    inline(cell.strip())
                    for cell in value.strip().strip("|").split("|")
                ]

            head = row(line)
            i += 2
            body = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body.append(row(lines[i]))
                i += 1
            out.append(
                "<table><thead><tr>"
                + "".join(f"<th>{value}</th>" for value in head)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>"
                    + "".join(f"<td>{value}</td>" for value in values)
                    + "</tr>"
                    for values in body
                )
                + "</tbody></table>"
            )
        elif not line.strip():
            i += 1
        else:
            buf = [line]
            i += 1
            while (i < len(lines) and lines[i].strip()
                   and not re.match(
                       r"^(#{1,3}\s|```|\s*([-*]|\d+\.)\s)", lines[i])
                   and "|" not in lines[i]):
                buf.append(lines[i])
                i += 1
            out.append("<p>" + inline("<br>".join(buf)) + "</p>")
    return "".join(out)
