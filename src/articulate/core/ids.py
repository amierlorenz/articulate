"""ID helpers and string generators for Articulate."""
import re

import ulid

# Pattern for valid Articulate IDs (§3.7): ^[A-Za-z0-9][A-Za-z0-9._:-]*$
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def new_ulid() -> str:
    """Generate a new 26-character uppercase ULID string."""
    return str(ulid.ULID())


def is_valid_id(val: str) -> bool:
    """Check if a string matches the Articulate ID pattern."""
    if not val:
        return False
    return bool(ID_PATTERN.match(val))


def slugify(text: str) -> str:
    """Convert text to a lowercase hyphen-separated slug."""
    # Convert non-alphanumeric characters to hyphens
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s


def collision_suffix(base: str, existing: set[str] | list[str], sep: str = " (", close: str = ")") -> str:
    """If base is in existing, find the next available suffix e.g. 'Note (2)'."""
    existing_set = set(existing)
    if base not in existing_set:
        return base

    idx = 2
    while True:
        candidate = f"{base}{sep}{idx}{close}"
        if candidate not in existing_set:
            return candidate
        idx += 1


def item_id(source: str, native_id: str, version: str = "") -> str:
    """Construct an item ID from source, native_id, and optional version."""
    src = slugify(source)
    # native_id might already be formatted, but ensure it only has valid characters
    nat = re.sub(r"[^a-zA-Z0-9._:-]+", "-", native_id).strip("-")
    if version:
        ver = re.sub(r"[^a-zA-Z0-9._:-]+", "-", version).strip("-")
        res = f"{src}:{nat}:{ver}"
    else:
        res = f"{src}:{nat}"
    return res


def normalise_course_key(text: str) -> str:
    """
    Normalise a course key from calendar event description (§3.2):
    Take the event description, strip HTML (<br> and block tags end a line),
    take the first non-empty line, lowercase it, and drop everything but a–z and 0–9.
    """
    # Replace <br>, <p>, </div>, </li>, </h...> with newlines
    stripped = re.sub(r"(?i)<br\s*/?>|</?p>|</div>|</li>|</h[1-6]>", "\n", text)
    # Strip any remaining HTML tags
    stripped = re.sub(r"<[^>]+>", "", stripped)

    lines = [line.strip() for line in stripped.splitlines()]
    first_non_empty = ""
    for line in lines:
        if line:
            first_non_empty = line
            break

    # Lowercase and keep only a-z and 0-9
    res = re.sub(r"[^a-z0-9]", "", first_non_empty.lower())
    return res
