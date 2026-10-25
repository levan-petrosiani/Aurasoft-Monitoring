"""Standards documents -> clauses: the Berlin Group PDF and the Georgian Word guides, split into numbered clauses, and a
check that a quoted sentence really occurs in a clause. No AI and no database here."""

from .clause import Clause
from .quote import check as check_quote, normalize as normalize_text, occurs as quote_occurs
from .sources import StandardSource, StandardSources, file_hash

__all__ = ["Clause", "StandardSource", "StandardSources", "check_quote", "file_hash", "normalize_text", "quote_occurs"]
