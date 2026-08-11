import re
from collections import Counter


LABEL = r"(?:[^\[\]\n]|\[[^\[\]\n]*\])*"
IMAGE = re.compile(rf"!\[{LABEL}\]\((?:[^()\n]|\([^()\n]*\))*\)")
LINK = re.compile(rf"\[({LABEL})\]\((?:[^()\n]|\([^()\n]*\))*\)")
REFERENCE_LINK = re.compile(r"\[([^\]\n]+)\]\[[^\]\n]*\]")
AUTOLINK = re.compile(r"<https?://[^>\s]+>", re.I)
BARE_URL = re.compile(r"https?://[^\s)>]+", re.I)
WWW_URL = re.compile(r"\bwww\.[^\s)>]+", re.I)
LINK_ONLY = re.compile(
    rf"^\s*(?:[-*+]\s+)?\[{LABEL}\]\((?:[^()\n]|\([^()\n]*\))*\)\s*$"
)
NAV_LINKS = re.compile(
    r"^\s*(?:\[[^\]\n]+\]\([^\n]+?\)\s*[|•-]?\s*){2,}$"
)
CHECKBOX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]")
ORPHAN_LINK = re.compile(r"^\s*\]\([^\n)]*\)\s*")
MARKUP_ONLY = re.compile(r"^[\s*_~`#>|:.-]+$")
FILE_DOWNLOAD = re.compile(
    r"\[(?:PDF\s*-\s*)?[<>]?\s*\d+(?:\.\d+)?\s*(?:KB|MB|GB)\]\s*$",
    re.I,
)
CONTENTS = re.compile(r"(?:table of )?contents", re.I)
ORDERED_ENTRY = re.compile(r"^\s*\d+[.)]\s+\S")
SPACED_HEADER = re.compile(r"(?:\b[A-Z]\s+){5,}[A-Z]\b")
EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
WORD = re.compile(r"\b[\w’'-]+\b")
NUMBERED_CITATION = re.compile(r"^(?:\[\d+\]|\d{1,3}[.)]?)\s+")
NUMBERED_CITATIONS = re.compile(r"(?:^|\s)(?:\[\d+\]|\d{1,3}[.)]?)(?=\s+)")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
STRUCTURAL = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```|\|)")
DEFINITION_LABELS = {"audiences", "categories", "download", "program", "written by"}
TRAILING_SECTIONS = {
    "author",
    "authors",
    "bibliography",
    "cited by",
    "endnotes",
    "footnotes",
    "further reading",
    "more from brookings",
    "newsletter",
    "references",
    "related content",
    "recommended",
    "tags",
    "topics",
}
TRAILING_SECTION = re.compile(
    rf"^(?:{'|'.join(re.escape(value) for value in TRAILING_SECTIONS)})"
    r"(?:\s*\(\d+\))?$"
)
REFERENCE_SECTIONS = {
    "bibliography",
    "cited by",
    "endnotes",
    "footnotes",
    "references",
}
NOISE = {
    "breadcrumb",
    "chamber of origin",
    "committees",
    "congress (years)",
    "facebook",
    "image",
    "linkedin",
    "legislation and law numbers",
    "legislation numbers",
    "reset form search",
    "search only:",
    "share",
    "sponsors/cosponsors",
    "text versions tip",
    "tip",
    "twitter",
    "within library",
    "words & phrases",
    "x (twitter)",
}


def normalize_markdown(content: str, *, title: str | None = None) -> str:
    source = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    occurrences = Counter(" ".join(line.split()).casefold() for line in source)
    lines: list[str] = []
    previous = ""
    skip_definition_value = False
    skip_contents = False
    title_labels = {
        value.strip("#*_ -").casefold()
        for value in ((title or ""), *((title or "").split("|", 1)[:1]))
        if value.strip()
    }
    for raw_line in source:
        stripped = raw_line.strip()
        label = stripped.strip("#*_ -").casefold()
        plain_label = label.rstrip(":")
        if CONTENTS.fullmatch(plain_label):
            skip_contents = True
            continue
        if skip_contents:
            if not stripped or ORDERED_ENTRY.match(stripped):
                continue
            skip_contents = False
        if (
            stripped
            and occurrences[" ".join(stripped.split()).casefold()] >= 3
            and not STRUCTURAL.match(stripped)
            and not re.search(r"[.!?][\"')\]]?$", stripped)
        ):
            continue
        trailing_section = bool(
            TRAILING_SECTION.fullmatch(plain_label)
        ) or plain_label.startswith(("newsletter ", "sign up", "subscribe"))
        if trailing_section:
            section_name = re.sub(r"\s*\(\d+\)$", "", plain_label)
            if (
                section_name in REFERENCE_SECTIONS
                or len(WORD.findall(" ".join(lines))) >= 40
            ):
                break
            skip_definition_value = plain_label in {"author", "authors"}
            continue
        if skip_definition_value:
            if not stripped:
                continue
            skip_definition_value = False
            continue
        if plain_label in DEFINITION_LABELS:
            skip_definition_value = True
            continue
        if (
            CHECKBOX.match(raw_line)
            or LINK_ONLY.match(raw_line)
            or NAV_LINKS.match(raw_line)
            or label in NOISE
            or label.startswith("examples:")
            or label.startswith("working paper number:")
            or "[x]" in label
            or FILE_DOWNLOAD.search(stripped)
        ):
            continue
        line = IMAGE.sub("", raw_line)
        line = LINK.sub(r"\1", line)
        line = REFERENCE_LINK.sub(r"\1", line)
        line = ORPHAN_LINK.sub("", line)
        line = AUTOLINK.sub("", line)
        line = BARE_URL.sub("", line)
        line = WWW_URL.sub("", line)
        line = SPACED_HEADER.sub("", line)
        line = line.replace("[]()", "").replace("**", "").replace("__", "")
        line = re.sub(r"[ \t]+", " ", line).strip()
        normalized = line.casefold()
        if not line or MARKUP_ONLY.fullmatch(line):
            if lines and lines[-1]:
                lines.append("")
            continue
        semantic = line.strip("#*_ -").casefold()
        if any(
            len(value) >= 20 and semantic == value
            for value in title_labels
        ):
            continue
        if normalized == previous or semantic == previous:
            continue
        lines.append(line)
        previous = semantic
    return _clean_blocks(lines)


def _clean_blocks(lines: list[str]) -> str:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in (*lines, ""):
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    cleaned = []
    for block in blocks:
        if sum(len(EMAIL.findall(line)) for line in block) >= 2:
            continue
        citations = sum(_citation_line(line) for line in block)
        reference_numbers = sum(
            len(NUMBERED_CITATIONS.findall(line)) for line in block
        )
        if max(citations, reference_numbers) >= 2 and citations / len(block) >= 0.6:
            continue
        cleaned.append(
            "\n".join(block)
            if any(STRUCTURAL.match(line) for line in block)
            else " ".join(block)
        )
    return "\n\n".join(cleaned).strip()


def _citation_line(line: str) -> bool:
    lowered = line.casefold()
    return bool(
        NUMBERED_CITATION.match(line)
        and (
            YEAR.search(line)
            or "ibid" in lowered
            or " et al" in lowered
            or "journal" in lowered
            or "working paper" in lowered
            or "available at" in lowered
        )
    )
