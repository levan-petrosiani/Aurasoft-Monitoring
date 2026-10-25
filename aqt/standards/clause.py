"""One numbered section of a standard, e.g. BG-IG 6.3.1.2 "Consent Request on Account List ..."."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Clause:
    standard: str        # BG-IG, GE-XS2A
    version: str
    number: str          # as printed: 6.3.1.2
    title: str
    text: str            # body of the section only (not its sub-sections)
    language: str        # en | ka
    order: int = 0       # position in the document
    page: int | None = None  # first page (PDF sources)

    @property
    def level(self) -> int:
        return self.number.count(".") + 1

    @property
    def parent(self) -> str | None:
        return self.number.rsplit(".", 1)[0] if "." in self.number else None

    @property
    def key(self) -> str:
        """Stable key: BG-IG:1.3.16:6.3.1.2"""
        return f"{self.standard}:{self.version}:{self.number}"

    def with_(self, **changes) -> "Clause":
        return replace(self, **changes)
