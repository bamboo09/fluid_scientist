"""Study message splitter.

When a researcher pastes several study descriptions into a single chat
message they are usually formatted as a numbered list, e.g.::

    1. Flow over a cylinder at Re = 100
    2. Same geometry but at Re = 1000
    3. Add surface roughness to case 2

The :class:`StudySplitter` detects such lists and returns one text block
per study so that each can be decomposed independently.
"""

from __future__ import annotations

import re


class StudySplitter:
    """Detects and splits multi-study natural-language input.

    The splitter uses regex heuristics to recognise numbered lists.  It
    supports the two most common delimiters -- ``1.`` (period) and ``1)``
    (closing parenthesis) -- and works with both inline lists
    (``"1. A 2. B"``) and newline-separated lists.  The detected numeric
    labels must form a sequence starting at 1 (``1, 2, 3, ...``) to avoid
    false positives from incidental numbers such as Reynolds values or
    decimal quantities.
    """

    # Regex matching a single numbered-list marker.
    #
    # Components:
    #   (?<![.\\d])  -- negative lookbehind: the digit must not be
    #                   preceded by a "." or another digit, filtering out
    #                   the integer part of decimals such as "2.5".
    #   (\\d+)       -- one or more digits (captured so the label can be
    #                   validated as a sequence).
    #   [.)]         -- the delimiter: either a period or a closing paren.
    #   \\s+         -- mandatory trailing whitespace so that "2.5" is
    #                   never mistaken for marker "2.".
    _MARKER_RE: re.Pattern[str] = re.compile(r"(?<![.\d])(\d+)[.)]\s+")

    def split(self, user_message: str) -> list[str]:
        """Split *user_message* into individual study text blocks.

        Detects numbered lists (e.g. ``"1. ... 2. ..."`` or
        ``"1) ... 2) ..."``) and returns one stripped text block per
        detected study.

        If no numbered list is detected -- or the detected numbers do not
        form a clean ``1, 2, 3, ...`` sequence -- the original message is
        returned unchanged inside a single-element list so the caller can
        treat it as a single study.

        Parameters
        ----------
        user_message:
            The raw user message that may contain one or more studies.

        Returns
        -------
        list[str]
            One element per detected study, with the leading list marker
            removed and surrounding whitespace stripped.  Never empty; a
            blank input yields ``[""]``.
        """
        if not user_message.strip():
            return [user_message]

        matches = list(self._MARKER_RE.finditer(user_message))
        # A single marker (or none) is not enough to call this a list.
        if len(matches) < 2:
            return [user_message]

        numbers = [int(m.group(1)) for m in matches]
        if not self._is_sequential_from_one(numbers):
            return [user_message]

        # Slice the text between consecutive markers.  The content of each
        # block starts right after the marker's trailing whitespace and ends
        # just before the next marker begins.
        blocks: list[str] = []
        for index, match in enumerate(matches):
            start = match.end()
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(user_message)
            )
            block = user_message[start:end].strip()
            if block:
                blocks.append(block)

        return blocks if blocks else [user_message]

    @staticmethod
    def _is_sequential_from_one(numbers: list[int]) -> bool:
        """Return ``True`` when *numbers* equals ``[1, 2, ..., n]`` with n >= 2.

        Requiring a clean sequence starting at 1 is the main guard against
        false positives: incidental numbers in free text (Reynolds values,
        mesh cell counts, etc.) rarely happen to line up as ``1, 2, 3``.
        """
        if len(numbers) < 2 or numbers[0] != 1:
            return False
        return all(actual == expected for expected, actual in enumerate(numbers, start=1))


__all__ = ["StudySplitter"]
