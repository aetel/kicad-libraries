#!/usr/bin/env python3
"""
AETEL KiCad PCM package builder.

Generates:
    build/aetel-kicad-addon.zip
    pcm/packages.json
    pcm/repository.json

Expected source directories:
    AETEL.pretty/          Footprint libraries
    AETEL.3dshapes/        3D model libraries
    symbols/               KiCad symbol libraries
    resources/icon.png     PCM package icon (64x64 PNG)

Usage:
    python3 scripts/package.py 0.2.0
    python3 scripts/package.py 0.2.0 --status testing
    python3 scripts/package.py 0.2.0 --status stable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import time
import zipfile

from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

PCM_DIR = ROOT / "pcm"
BUILD_DIR = ROOT / "build"

METADATA_TEMPLATE = PCM_DIR / "metadata.json"
PACKAGES_FILE = PCM_DIR / "packages.json"
REPOSITORY_FILE = PCM_DIR / "repository.json"

ZIP_NAME = "aetel-kicad-addon.zip"
ZIP_PATH = BUILD_DIR / ZIP_NAME

PACKAGE_IDENTIFIER = "com.github.aetel.kicad-libraries"

GITHUB_REPOSITORY = "aetel/kicad-libraries"

PACKAGES_URL = (
    "https://raw.githubusercontent.com/"
    "aetel/kicad-libraries/main/pcm/packages.json"
)

RELEASE_URL = (
    "https://github.com/"
    + GITHUB_REPOSITORY
    + "/releases/download/{tag}/"
    + ZIP_NAME
)

PCM_SCHEMA = "https://go.kicad.org/pcm/schemas/v2"

DEFAULT_KICAD_VERSION = "10.0"

DEFAULT_STATUS = "testing"

CONTENT_DIRECTORIES = (
    "footprints",
    "3dmodels",
    "symbols",
    "resources",
    "templates",
)

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


# ============================================================
# GENERAL UTILITIES
# ============================================================

def load_json(filename: Path) -> dict[str, Any]:
    """Load a JSON file."""

    if not filename.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    with filename.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(filename: Path, data: dict[str, Any]) -> None:
    """Write formatted JSON."""

    filename.parent.mkdir(parents=True, exist_ok=True)

    with filename.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )
        file.write("\n")


def validate_version(version: str) -> None:
    """Validate a semantic numeric version."""

    pattern = r"^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$"

    if not re.fullmatch(pattern, version):
        raise ValueError(
            f"Invalid version: {version}\n"
            "Expected format: major[.minor[.patch]]"
        )


def sha256_file(filename: Path) -> str:
    """Calculate the SHA-256 hash of a file."""

    digest = hashlib.sha256()

    with filename.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def write_zip_directory(
    archive: zipfile.ZipFile,
    directory_name: str
) -> None:
    """Add a directory entry to the ZIP."""

    info = zipfile.ZipInfo(
        directory_name.rstrip("/") + "/",
        date_time=ZIP_TIMESTAMP
    )

    info.create_system = 3
    info.external_attr = (0o40755 << 16) | 0x10

    archive.writestr(info, b"")


def add_file_to_zip(
    archive: zipfile.ZipFile,
    source: Path,
    archive_name: str
) -> None:
    """Add a file using a deterministic ZIP entry."""

    info = zipfile.ZipInfo(
        archive_name.replace("\\", "/"),
        date_time=ZIP_TIMESTAMP
    )

    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16

    archive.writestr(
        info,
        source.read_bytes(),
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9
    )


# ============================================================
# SOURCE VALIDATION
# ============================================================

def validate_icon() -> Path:
    """
    Check that resources/icon.png exists and is a valid
    64x64 PNG image.
    """

    icon = ROOT / "resources" / "icon.png"

    if not icon.is_file():
        raise FileNotFoundError(
            "Missing resources/icon.png"
        )

    with icon.open("rb") as file:
        header = file.read(24)

    png_signature = b"\x89PNG\r\n\x1a\n"

    if len(header) < 24 or header[:8] != png_signature:
        raise ValueError(
            "resources/icon.png is not a valid PNG file."
        )

    width, height = struct.unpack(">II", header[16:24])

    if width != 64 or height != 64:
        raise ValueError(
            f"Invalid icon dimensions: {width}x{height}. "
            "The PCM icon must be 64x64 pixels."
        )

    return icon


def collect_library_directories(
    extension: str,
    destination: str
) -> list[tuple[Path, str]]:
    """
    Find library directories.

    Supports both layouts:

        AETEL.pretty/

    and:

        footprints/AETEL.pretty/

    Returns source directory and ZIP destination.
    """

    result: list[tuple[Path, str]] = []

    container = ROOT / destination

    if container.is_dir():
        libraries = sorted(
            path for path in container.iterdir()
            if path.is_dir() and path.name.endswith(extension)
        )
    else:
        libraries = sorted(
            path for path in ROOT.iterdir()
            if path.is_dir() and path.name.endswith(extension)
        )

    for library in libraries:
        result.append(
            (library, f"{destination}/{library.name}")
        )

    return result

def collect_templates_directory() -> Path | None:
    """Find the templates source directory."""

    directory = ROOT / "template"

    if directory.is_dir():
        return directory

    return None

def collect_symbols_directory() -> Path | None:
    """Find the symbols source directory."""

    directory = ROOT / "symbols"

    if directory.is_dir():
        return directory

    return None


def validate_sources() -> dict[str, Any]:
    """Validate all package source directories."""
    
    templates = collect_templates_directory()

    footprints = collect_library_directories(
        ".pretty",
        "footprints"
    )

    models = collect_library_directories(
        ".3dshapes",
        "3dmodels"
    )

    symbols = collect_symbols_directory()

    symbol_files = []

    if symbols:
        symbol_files = sorted(
            path for path in symbols.rglob("*.kicad_sym")
            if path.is_file()
        )

    if not footprints and not models and not symbol_files:
        raise ValueError(
            "The package must contain at least one footprint "
            "library, 3D model library or symbol library."
        )

    icon = validate_icon()

    resources = ROOT / "resources"

    if not resources.is_dir():
        raise FileNotFoundError("Missing resources directory.")

    return {
        "footprints": footprints,
        "models": models,
        "symbols": symbols,
        "symbol_files": symbol_files,
        "icon": icon,
        "resources": resources,
        "templates": templates,
    }


# ============================================================
# INTERNAL PACKAGE METADATA
# ============================================================

def create_internal_metadata(
    template: dict[str, Any],
    version: str,
    status: str,
    kicad_version: str
) -> dict[str, Any]:
    """
    Create metadata.json stored inside the ZIP.

    The internal metadata contains only the version being
    packaged. Download information belongs in packages.json.
    """

    metadata = template.copy()

    metadata["$schema"] = PCM_SCHEMA
    metadata["versions"] = [{
        "version": version,
        "status": status,
        "kicad_version": kicad_version,
    }]

    return metadata


# ============================================================
# ZIP GENERATION
# ============================================================

def add_directory_contents(
    archive: zipfile.ZipFile,
    source: Path,
    archive_directory: str
) -> int:
    """Add all files inside a directory recursively."""

    total_size = 0

    # write_zip_directory(archive, archive_directory)

    files = sorted(
        path for path in source.rglob("*")
        if path.is_file()
    )

    for filename in files:
        if filename.is_symlink():
            raise ValueError(
                f"Symbolic links are not supported: {filename}"
            )

        relative = filename.relative_to(source)

        archive_name = (
            f"{archive_directory}/{relative.as_posix()}"
        )

        add_file_to_zip(
            archive,
            filename,
            archive_name
        )

        total_size += filename.stat().st_size

    return total_size


def generate_zip(
    version: str,
    status: str,
    kicad_version: str,
    sources: dict[str, Any],
    template: dict[str, Any]
) -> tuple[int, int]:
    """
    Generate the PCM archive.

    Returns:
        compressed ZIP size
        uncompressed installation size
    """

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    internal_metadata = create_internal_metadata(
        template,
        version,
        status,
        kicad_version
    )

    metadata_bytes = (
        json.dumps(
            internal_metadata,
            indent=2,
            ensure_ascii=False
        ) + "\n"
    ).encode("utf-8")

    install_size = 0

    with zipfile.ZipFile(
        ZIP_PATH,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9
    ) as archive:

        # metadata.json must be at the archive root.
        info = zipfile.ZipInfo(
            "metadata.json",
            date_time=ZIP_TIMESTAMP
        )

        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16

        archive.writestr(
            info,
            metadata_bytes,
            compress_type=zipfile.ZIP_DEFLATED,
            compresslevel=9
        )

        install_size += len(metadata_bytes)

        # Always include the four requested directories.
        for directory in CONTENT_DIRECTORIES:
            write_zip_directory(archive, directory)
            
        # Templates.
        for source, destination in sources["templates"]:
            install_size += add_directory_contents(
                archive,
                source,
                destination
            )

        # Footprint libraries.
        for source, destination in sources["footprints"]:
            install_size += add_directory_contents(
                archive,
                source,
                destination
            )

        # 3D model libraries.
        for source, destination in sources["models"]:
            install_size += add_directory_contents(
                archive,
                source,
                destination
            )

        # Symbol libraries.
        if sources["symbols"]:
            for filename in sources["symbol_files"]:
                relative = filename.relative_to(
                    sources["symbols"]
                )

                archive_name = (
                    "symbols/" + relative.as_posix()
                )

                add_file_to_zip(
                    archive,
                    filename,
                    archive_name
                )

                install_size += filename.stat().st_size

        # Package resources, including resources/icon.png.
        install_size += add_directory_contents(
            archive,
            sources["resources"],
            "resources"
        )

    zip_size = ZIP_PATH.stat().st_size

    return zip_size, install_size


# ============================================================
# PACKAGES.JSON
# ============================================================

def create_version_entry(
    version: str,
    status: str,
    kicad_version: str,
    zip_size: int,
    install_size: int,
    zip_sha256: str
) -> dict[str, Any]:
    """Create an external PCM version entry."""

    tag = f"v{version}"

    return {
        "version": version,
        "status": status,
        "kicad_version": kicad_version,
        "download_url": RELEASE_URL.format(tag=tag),
        "download_sha256": zip_sha256,
        "download_size": zip_size,
        "install_size": install_size,
    }


def update_packages_json(
    template: dict[str, Any],
    version_entry: dict[str, Any]
) -> None:
    """
    Add or replace a version in packages.json.

    All previously published versions are preserved.
    """

    if PACKAGES_FILE.exists():
        package_index = load_json(PACKAGES_FILE)
    else:
        package_index = {
            "$schema": (
                "https://go.kicad.org/pcm/schemas/v2"
                "#/definitions/PackageArray"
            ),
            "packages": [],
        }

    package_index.setdefault("packages", [])

    identifier = template["identifier"]

    package = next(
        (
            item for item in package_index["packages"]
            if item.get("identifier") == identifier
        ),
        None
    )

    if package is None:
        package = template.copy()
        package["versions"] = []
        package_index["packages"].append(package)

    # Refresh descriptive metadata from the canonical template.
    for key, value in template.items():
        if key != "versions":
            package[key] = value

    versions = package.setdefault("versions", [])

    # Replace the same version if the workflow is rerun.
    versions[:] = [
        item for item in versions
        if item.get("version") != version_entry["version"]
    ]

    # Newest version first.
    versions.insert(0, version_entry)

    package["versions"] = versions

    package_index["$schema"] = (
        "https://go.kicad.org/pcm/schemas/v2"
        "#/definitions/PackageArray"
    )

    save_json(PACKAGES_FILE, package_index)


# ============================================================
# REPOSITORY.JSON
# ============================================================

def update_repository_json(
    template: dict[str, Any]
) -> tuple[str, int]:
    """
    Update repository.json.

    The timestamp is only changed when packages.json changes.
    """

    packages_hash = sha256_file(PACKAGES_FILE)

    if REPOSITORY_FILE.exists():
        repository = load_json(REPOSITORY_FILE)
    else:
        repository = {}

    previous_packages = repository.get("packages", {})

    previous_hash = previous_packages.get("sha256")
    previous_timestamp = previous_packages.get("update_timestamp")

    if previous_hash == packages_hash and previous_timestamp:
        timestamp = previous_timestamp
    else:
        timestamp = int(time.time())

    repository.update({
        "$schema": (
            "https://go.kicad.org/pcm/schemas/v2"
            "#/definitions/Repository"
        ),
        "schema_version": 2,
        "name": template["name"],
        "maintainer": template.get(
            "maintainer",
            template.get("author", {})
        ),
        "packages": {
            "url": PACKAGES_URL,
            "update_timestamp": timestamp,
            "sha256": packages_hash,
        }
    })

    save_json(REPOSITORY_FILE, repository)

    return packages_hash, timestamp


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description="Build and update the AETEL KiCad PCM package."
    )

    parser.add_argument(
        "version",
        help="Package version, e.g. 0.2.0"
    )

    parser.add_argument(
        "--status",
        choices=[
            "stable",
            "testing",
            "development",
            "deprecated"
        ],
        default=DEFAULT_STATUS,
        help="PCM release status."
    )

    parser.add_argument(
        "--kicad-version",
        default=DEFAULT_KICAD_VERSION,
        help="Minimum KiCad version."
    )

    args = parser.parse_args()

    try:
        version = args.version.strip()

        validate_version(version)

        print()
        print("========================================")
        print(" AETEL KiCad PCM package builder")
        print("========================================")
        print()

        print(f"Version:       {version}")
        print(f"Status:        {args.status}")
        print(f"KiCad version: {args.kicad_version}")
        print()

        # Load canonical package metadata.
        template = load_json(METADATA_TEMPLATE)

        if template.get("identifier") != PACKAGE_IDENTIFIER:
            raise ValueError(
                "Unexpected package identifier in pcm/metadata.json. "
                f"Expected: {PACKAGE_IDENTIFIER}"
            )

        template["$schema"] = PCM_SCHEMA

        # Validate source directories and icon.
        print("[1/5] Checking source directories...")

        sources = validate_sources()

        print(
            f"      Footprint libraries: "
            f"{len(sources['footprints'])}"
        )

        print(
            f"      3D model libraries: "
            f"{len(sources['models'])}"
        )

        print(
            f"      Symbol files: "
            f"{len(sources['symbol_files'])}"
        )

        print("      Resources: OK")
        print("      Icon: resources/icon.png (64x64)")
        print()

        # Generate ZIP.
        print("[2/5] Generating ZIP package...")

        zip_size, install_size = generate_zip(
            version,
            args.status,
            args.kicad_version,
            sources,
            template
        )

        zip_hash = sha256_file(ZIP_PATH)

        print(f"      File: {ZIP_PATH.relative_to(ROOT)}")
        print(f"      Download size: {zip_size} bytes")
        print(f"      Install size:  {install_size} bytes")
        print(f"      SHA-256: {zip_hash}")
        print()

        # Update package index.
        print("[3/5] Updating pcm/packages.json...")

        version_entry = create_version_entry(
            version,
            args.status,
            args.kicad_version,
            zip_size,
            install_size,
            zip_hash
        )

        update_packages_json(
            template,
            version_entry
        )

        print("      packages.json updated.")
        print()

        # Update repository descriptor.
        print("[4/5] Updating pcm/repository.json...")

        packages_hash, timestamp = update_repository_json(
            template
        )

        print(f"      packages.json SHA-256: {packages_hash}")
        print(f"      Update timestamp: {timestamp}")
        print("      repository.json updated.")
        print()

        # Final summary.
        print("[5/5] Package generation completed.")
        print()
        print("Generated files:")
        print(f"  {ZIP_PATH.relative_to(ROOT)}")
        print(f"  {PACKAGES_FILE.relative_to(ROOT)}")
        print(f"  {REPOSITORY_FILE.relative_to(ROOT)}")
        print()
        print("Release asset URL:")
        print(RELEASE_URL.format(tag=f"v{version}"))
        print()
        print("Remember to publish the ZIP as a GitHub Release asset.")
        print()

        return 0

    except Exception as error:
        print()
        print(f"ERROR: {error}", file=sys.stderr)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
