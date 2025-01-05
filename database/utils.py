"""Utility functions for the database module."""

import os
from datetime import datetime, timezone

import aiohttp

from database.schema import VersionRecord


def utcnow() -> str:
    """Returns the current time in UTC in the format of a string."""
    current_utc = datetime.now(tz=timezone.utc)
    formatted_time = current_utc.strftime("%Y-%m-%dT%H:%M:%S")
    return formatted_time


async def upload_to_catbox(filename: str, file: bytes, mimetype: str) -> str:
    """Uploads a file to catbox.moe asynchronously.

    Args:
        filename: The name of the file.
        file: The file to upload.
        mimetype: The mimetype of the file.

    Returns:
        The link to the uploaded file.
    """
    catbox_url = "https://catbox.moe/user/api.php"
    userhash = os.getenv("CATBOX_USERHASH")

    data = aiohttp.FormData()
    data.add_field('reqtype', 'fileupload')
    if userhash:
        data.add_field('userhash', userhash)
    data.add_field('fileToUpload', file, filename=filename, content_type=mimetype)

    async with aiohttp.ClientSession() as session:
        async with session.post(catbox_url, data=data) as response:
            response_text = await response.text()
            return response_text


def get_version_string(version: VersionRecord, no_edition: bool = False) -> str:
    """Returns a formatted version string."""
    if no_edition:
        return f"{version['major_version']}.{version['minor_version']}.{version['patch_number']}"
    else:
        return f"{version['edition']} {version['major_version']}.{version['minor_version']}.{version['patch_number']}"


def get_version_tuple(version: VersionRecord) -> tuple[str, int, int, int]:
    """Returns a version tuple."""
    return version["edition"], version["major_version"], version["minor_version"], version["patch_number"]


def parse_version_string(version_string: str) -> tuple[str, int, int, int]:
    """Parses a version string into its components.

    A version string is formatted as follows:
    ["Java" | "Bedrock"] major_version.minor_version.patch_number
    """

    try:
        edition_and_major, minor, patch = version_string.split(".")
    except ValueError:
        raise ValueError("Invalid version string format.")

    if " " in edition_and_major:
        edition, major = edition_and_major.split(" ")
    else:
        edition = "Java"
        major = edition_and_major

    return edition, int(major), int(minor), int(patch)


def parse_version(version_str: str):
    """Parse a version string 'X.Y.Z' into a tuple of integers (X, Y, Z)."""
    major, minor, patch = version_str.split('.')
    return int(major), int(minor), int(patch)


async def filter_versions(spec: str) -> list[str]:
    """Return all versions that match the version specification."""
    from database import DatabaseManager
    all_versions = await DatabaseManager.fetch_versions_list("Java")
    # Convert each version in all_versions into a tuple for easy comparison
    all_version_tuples = [(v["major_version"], v["minor_version"], v["patch_number"]) for v in all_versions]

    # Split the spec by commas: e.g. "1.14 - 1.16.1, 1.17, 1.19+"
    parts = [part.strip() for part in spec.split(',')]

    valid_tuples: list[tuple[int, int, int]] = []

    for part in parts:
        # Case 1: range like "1.14 - 1.16.1"
        if '-' in part:
            start_str, end_str = [p.strip() for p in part.split('-')]
            start_tuple = parse_version(start_str) if start_str.count('.') == 2 else parse_version(start_str + '.0')
            end_tuple = parse_version(end_str) if end_str.count('.') == 2 else parse_version(end_str + '.0')

            for v_tuple in all_version_tuples:
                if start_tuple <= v_tuple <= end_tuple:
                    valid_tuples.append(v_tuple)

        # Case 2: trailing plus like "1.19+"
        elif part.endswith('+'):
            base_str = part[:-1].strip()
            # If user just wrote "1.19+", assume "1.19.0"
            if base_str.count('.') == 1:
                base_str += '.0'
            base_tuple = parse_version(base_str)

            for v_tuple in all_version_tuples:
                if v_tuple >= base_tuple:
                    valid_tuples.append(v_tuple)

        # Case 3: exact version or prefix, e.g. "1.17" or "1.17.1"
        else:
            subparts = part.split('.')
            # If only major.minor specified (like "1.17"), match all "1.17.x"
            if len(subparts) == 2:
                major, minor = map(int, subparts)
                for v_tuple in all_version_tuples:
                    if v_tuple[0] == major and v_tuple[1] == minor:
                        valid_tuples.append(v_tuple)
            # If a full version specified (like "1.17.1"), match exactly that version
            elif len(subparts) == 3:
                v_tuple = tuple(map(int, subparts))
                if v_tuple in all_version_tuples:
                    valid_tuples.append(v_tuple)
            else:
                # Optionally, handle malformed inputs or major-only specs
                pass

    return [f"{major}.{minor}.{patch}" for major, minor, patch in valid_tuples]
