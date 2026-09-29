"""Output path validation never trusts the requested filename or directory."""

import pytest

from aimedia.artifacts import publish_output


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../outside",
        "a/b",
        r"a\b",
        "/absolute",
        r"C:\absolute",
        "CON",
        "com1",
        "LPT²",
        "name. ",
        "name.txt",
        "name:",
        "name\x00",
        "name\x1f",
        "name\x7f",
        "name\x80",
    ],
)
def test_unsafe_basename_is_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe output basename"):
        publish_output(b"verified", tmp_path, name, "png", managed=False)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("extension", [".png", "PNG", "exe", "png/../../bad", "", "png:stream"])
def test_unsafe_extension_is_rejected(tmp_path, extension):
    with pytest.raises(ValueError, match="Unsupported output extension"):
        publish_output(b"verified", tmp_path, "safe", extension, managed=False)
    assert not list(tmp_path.iterdir())


def test_symlinked_output_directory_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Creating directory symlinks requires privileges on this platform")
    with pytest.raises(ValueError, match="Symlinked or junction output directory"):
        publish_output(b"verified", link, "safe", "png", managed=False)
    assert not list(real.iterdir())


def test_existing_junction_directory_is_rejected(tmp_path, monkeypatch):
    original_is_junction = type(tmp_path).is_junction

    def junction_at_output(component):
        return component == tmp_path or original_is_junction(component)

    monkeypatch.setattr(type(tmp_path), "is_junction", junction_at_output)
    with pytest.raises(ValueError, match="Symlinked or junction output directory"):
        publish_output(b"verified", tmp_path, "safe", "png", managed=False)
    assert not list(tmp_path.iterdir())


def test_existing_symlink_is_not_overwritten(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    link = tmp_path / "safe.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Creating file symlinks requires privileges on this platform")
    result = publish_output(b"verified", tmp_path, "safe", "png", managed=False)
    assert link.is_symlink()
    assert outside.read_bytes() == b"original"
    assert result.path.name == "safe_002.png"
