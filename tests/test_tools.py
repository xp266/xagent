import os

import pytest

from src.tools import edit, read


@pytest.fixture
def workdir(tmp_path):
    old = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(old)


def test_read_empty_file(workdir):
    (workdir / "empty.txt").write_text("")
    r = read.execute("empty.txt")
    assert r["metadata"].get("error") is None
    assert r["output"] == ""


def test_read_byte_cap(workdir):
    (workdir / "big.txt").write_text("\n".join("x" * 300 for _ in range(200)) + "\n")
    r = read.execute("big.txt")
    assert r["metadata"]["byte_cut"] is True
    assert r["metadata"]["truncated"] is True
    assert len(r["output"]) <= 60 * 1024


def test_read_numbers_lines(workdir):
    (workdir / "n.txt").write_text("aaa\nbbb\nccc\n")
    r = read.execute("n.txt")
    out = read.to_model_output(r)
    lines = out.split("\n")
    assert lines[0] == "(n.txt, 3 lines)"
    assert lines[1] == "1:aaa"
    assert lines[3] == "3:ccc"


def test_read_out_of_range(workdir):
    (workdir / "n.txt").write_text("aaa\n")
    r = read.execute("n.txt", offset=9)
    assert r["metadata"]["error"] is True


def test_edit_basic(workdir):
    (workdir / "t.txt").write_text("aaa\nbbb\nccc\n")
    e = edit.execute("t.txt", "bbb", "BBB")
    assert e["metadata"].get("error") is None
    assert (workdir / "t.txt").read_text() == "aaa\nBBB\nccc\n"


def test_edit_replace_all(workdir):
    (workdir / "m.txt").write_text("x\ny\nx\ny\n")
    e = edit.execute("m.txt", "x", "X", True)
    assert e["metadata"].get("error") is None
    assert (workdir / "m.txt").read_text() == "X\ny\nX\ny\n"


def test_edit_multiple_matches_refused(workdir):
    (workdir / "m.txt").write_text("x\ny\nx\ny\n")
    e = edit.execute("m.txt", "x", "X")
    assert e["metadata"]["error"] is True
    assert "multiple matches" in e["output"].lower()


def test_edit_bom_preserved_single(workdir):
    (workdir / "bom.txt").write_bytes(b"\xef\xbb\xbfprint('hello')\n")
    e = edit.execute("bom.txt", "print('hello')", "print('hi')")
    assert e["metadata"].get("error") is None
    data = (workdir / "bom.txt").read_bytes()
    assert data[:3] == b"\xef\xbb\xbf"
    assert data.count(b"\xef\xbb\xbf") == 1
    assert b"print('hi')" in data


def test_edit_directory_rejected(workdir):
    (workdir / "sub").mkdir()
    e = edit.execute("sub", "x", "y")
    assert e["metadata"]["error"] is True
    assert "directory" in e["output"].lower()


def test_edit_not_found(workdir):
    (workdir / "t.txt").write_text("aaa\n")
    e = edit.execute("t.txt", "zzz", "yyy")
    assert e["metadata"]["error"] is True
    assert "could not find" in e["output"].lower()


def test_edit_crlf_file(workdir):
    (workdir / "crlf.txt").write_bytes(b"aaa\r\nbbb\r\nccc\r\n")
    e = edit.execute("crlf.txt", "bbb", "BBB")
    assert e["metadata"].get("error") is None
    assert (workdir / "crlf.txt").read_bytes() == b"aaa\r\nBBB\r\nccc\r\n"
