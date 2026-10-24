"""standards/sources.yaml: the manifest of standards documents; downloading (with SHA-256 check) and reading them."""

import hashlib
import os
import urllib.request
from dataclasses import dataclass, field

import yaml

from . import docx_reader, pdf_reader
from .clause import Clause


@dataclass
class StandardSource:
    id: str
    title: str
    version: str
    url: str
    file: str                        # path below standards/sources/
    sha256: str
    language: str = "en"             # en | ka
    format: str = "pdf"              # pdf | docx
    publisher: str | None = None
    published: str | None = None
    page: str | None = None
    license: str | None = None
    # Regexes of running header/footer lines to drop from PDF text (repeated top-of-page lines are dropped anyway).
    page_furniture: list[str] = field(default_factory=list)
    # Corrections for headings the document itself prints wrongly: clause number -> title.
    titles: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "StandardSource":
        return StandardSource(
            id=d["id"], title=d.get("title", ""), version=str(d["version"]), url=d.get("url", ""), file=d["file"],
            sha256=d.get("sha256", ""), language=d.get("language", "en"), format=d.get("format", "pdf"),
            publisher=d.get("publisher"), published=str(d["published"]) if d.get("published") else None,
            page=d.get("page"), license=d.get("license"), page_furniture=list(d.get("pageFurniture") or []),
            titles={str(k): v for k, v in (d.get("titles") or {}).items()})


def file_hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StandardSources:
    def __init__(self, standards_directory: str):
        self.directory = standards_directory

    def load(self) -> list[StandardSource]:
        path = os.path.join(self.directory, "sources.yaml")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No standards manifest at {path}")
        with open(path, encoding="utf-8") as f:
            return [StandardSource.from_dict(d) for d in yaml.safe_load(f) or []]

    def path_of(self, source: StandardSource) -> str:
        return os.path.join(self.directory, "sources", source.file)

    def fetch(self, source: StandardSource) -> str:
        """Downloads the file if it is missing and checks its SHA-256. Returns what happened."""
        path = self.path_of(source)
        if os.path.isfile(path):
            self._verify(source, path)
            return "present, hash ok"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp = path + ".download"
        request = urllib.request.Request(source.url, headers={"User-Agent": "ApiTestTool"})
        with urllib.request.urlopen(request, timeout=300) as response, open(temp, "wb") as f:
            while chunk := response.read(1 << 20):
                f.write(chunk)
        try:
            self._verify(source, temp)
        except ValueError:
            os.remove(temp)
            raise
        os.replace(temp, path)
        return "downloaded, hash ok"

    def read(self, source: StandardSource) -> tuple[list[Clause], list[str]]:
        """Splits the downloaded file into clauses (after checking its hash)."""
        path = self.path_of(source)
        self._verify(source, path)
        if source.format == "pdf":
            result = pdf_reader.read(path, source.id, source.version, source.language, source.page_furniture)
            clauses, problems = result.clauses, list(result.problems)
        elif source.format == "docx":
            clauses, problems = docx_reader.read(path, source.id, source.version, source.language), []
        else:
            raise ValueError(f"{source.id}: unknown format '{source.format}' (pdf or docx)")

        for number, title in source.titles.items():
            index = next((i for i, c in enumerate(clauses) if c.number == number), -1)
            if index < 0:
                problems.append(f"Title correction for {number}: no such clause")
            else:
                clauses[index] = clauses[index].with_(title=title)
        return clauses, problems

    @staticmethod
    def _verify(source: StandardSource, path: str):
        actual = file_hash(path)
        if actual.lower() != source.sha256.lower():
            raise ValueError(f"{source.id} {source.version}: SHA-256 {actual} does not match the manifest ({source.sha256}). "
                             "The publisher may have changed the file: review it, then update sources.yaml.")
