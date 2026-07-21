"""Text normalization - the contract every DocumentLoader output satisfies.

Whatever the source format, loaders return text through normalize_text so
every downstream stage receives comparably clean input (ADR-018). The
rules, in order:

1. Decode/repair to valid UTF-8 (callers pass already-decoded str).
2. Strip a leading BOM if present.
3. Normalize line endings CRLF/CR -> LF.
4. Trim trailing whitespace on each line.
5. Collapse 3+ consecutive blank lines to a single blank line.
6. Strip leading/trailing blank lines from the whole document.

Rule 5 is deliberately conservative (it preserves single blank lines,
which carry paragraph structure) and is the one a future setting might
make configurable; for now it is fixed.
"""

import re

_BOM = "\ufeff"
_MULTI_BLANK = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Apply the normalization contract to already-decoded text."""
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    # CRLF and lone CR -> LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Trim trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Collapse 3+ blank lines (i.e. 3+ newlines) to one blank line
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip("\n")
