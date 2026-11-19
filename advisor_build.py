"""Small stdlib PEP 517 backend so installation needs no build downloads."""
import base64
import csv
import hashlib
import io
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).parent
NAME = "task_advisor-0.4.0"
INFO = NAME + ".dist-info"
METADATA = "Metadata-Version: 2.1\nName: task-advisor\nVersion: 0.4.0\nSummary: Local personal model router\nRequires-Python: >=3.10\n\n"
INGEST = ("__init__.py", "parsers.py", "readers.py", "privacy.py", "signals.py", "outcomes.py")


def source_extras():
    return [ROOT/"pyproject.toml", ROOT/"advisor_build.py", ROOT/"QUICKSTART.md", ROOT/"README.md", ROOT/".gitignore",
            *[ROOT/"docs"/name for name in ("ADVISOR_TECHNICAL.md", "ADVISOR_TECHNICAL.pdf", "ADVISOR_TECHNICAL.html", "build_advisor_pdf.py") if (ROOT/"docs"/name).exists()]]


def sources(tests=False):
    for p in sorted((ROOT/"advisor").rglob("*")):
        if not p.is_file() or p.suffix not in {".py", ".json", ".jsonl", ".md"}: continue
        if any(part in {"__pycache__", "state"} for part in p.parts): continue
        if not tests and "tests" in p.parts: continue
        yield p
    for name in INGEST: yield ROOT/"ingest"/name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = NAME + "-py3-none-any.whl"
    rows = []
    with zipfile.ZipFile(Path(wheel_directory)/name, "w", zipfile.ZIP_DEFLATED) as archive:
        def put(name, data):
            data = data.encode() if isinstance(data, str) else data
            archive.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append([name, "sha256=" + digest, len(data)])
        for path in sources(): put(path.relative_to(ROOT).as_posix(), path.read_bytes())
        put(INFO+"/METADATA", METADATA)
        put(INFO+"/WHEEL", "Wheel-Version: 1.0\nGenerator: advisor_build\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        put(INFO+"/entry_points.txt", "[console_scripts]\nadvisor = advisor.__main__:main\n")
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows + [[INFO+"/RECORD", "", ""]])
        archive.writestr(INFO+"/RECORD", output.getvalue())
    return name


def build_sdist(sdist_directory, config_settings=None):
    name = "task-advisor-0.4.0.tar.gz"
    with tarfile.open(Path(sdist_directory)/name, "w:gz") as archive:
        for path in [*sources(True), *source_extras(), *[p for p in (ROOT/"demo").rglob("*") if p.is_file() and "__pycache__" not in p.parts], *[p for p in (ROOT/".github").rglob("*") if p.is_file()]]:
            archive.add(path, arcname="task-advisor-0.4.0/" + path.relative_to(ROOT).as_posix())
    return name


if __name__ == "__main__":
    directory = ROOT/"dist"
    directory.mkdir(exist_ok=True)
    with zipfile.ZipFile(directory/"task-advisor-0.4.0.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in [*sources(True), *source_extras(), *[p for p in (ROOT/"demo").rglob("*") if p.is_file() and "__pycache__" not in p.parts], *[p for p in (ROOT/".github").rglob("*") if p.is_file()]]:
            archive.write(path, "task-advisor-0.4.0/" + path.relative_to(ROOT).as_posix())
