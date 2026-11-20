"""Tests for core.ids."""
from articulate.core.ids import (
    collision_suffix,
    is_valid_id,
    item_id,
    new_ulid,
    normalise_course_key,
    slugify,
)


def test_new_ulid():
    uid1 = new_ulid()
    uid2 = new_ulid()
    assert len(uid1) == 26
    assert is_valid_id(uid1)
    assert uid1 != uid2


def test_is_valid_id():
    assert is_valid_id("abc")
    assert is_valid_id("ABC-123_test.01:sub")
    assert not is_valid_id("")
    assert not is_valid_id("-abc")  # must start with alphanumeric
    assert not is_valid_id(".abc")
    assert not is_valid_id("abc/def")
    assert not is_valid_id("abc def")


def test_slugify():
    assert slugify("Hello World!") == "hello-world"
    assert slugify("MAT 305: Prob & Stats") == "mat-305-prob-stats"
    assert slugify("  --- Multiple   spaces & dashes --- ") == "multiple-spaces-dashes"
    assert slugify("already-slugified") == "already-slugified"


def test_collision_suffix():
    existing = {"Note", "Note (2)", "Note (3)"}
    assert collision_suffix("Note", existing) == "Note (4)"
    assert collision_suffix("New Note", existing) == "New Note"


def test_item_id():
    iid1 = item_id("canvas", "file-123", "v1")
    assert iid1 == "canvas:file-123:v1"
    assert is_valid_id(iid1)

    iid2 = item_id("pocket", "rec-456")
    assert iid2 == "pocket:rec-456"
    assert is_valid_id(iid2)


def test_normalise_course_key():
    # §3.2: strip HTML, take first non-empty line, lowercase, drop everything but a-z and 0-9
    assert normalise_course_key("MAT 305") == "mat305"
    assert normalise_course_key("CSC 301") == "csc301"
    assert normalise_course_key("CSC 430<br>Lecture 1") == "csc430"
    assert normalise_course_key("<p>PHY-203</p><span>Physics</span>") == "phy203"
    assert normalise_course_key("\n\n  MAT-305 \n Prob & Stats") == "mat305"
