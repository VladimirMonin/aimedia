"""Publication of already verified bytes through the output adapter."""

from concurrent.futures import ThreadPoolExecutor

from aimedia.artifacts import publish_output


def test_collision_preserves_existing_file_and_returns_final_metadata(tmp_path):
    existing = tmp_path / "image.png"
    existing.write_bytes(b"old")

    result = publish_output(b"new", tmp_path, "image", "png", managed=False)

    assert existing.read_bytes() == b"old"
    assert result.path == tmp_path / "image_002.png"
    assert result.path.read_bytes() == b"new"
    assert result.size_bytes == 3
    assert result.sha256 == "11507a0e2f5e69d5dfa40a62a1bd7b6ee57e6bcd85c67c9b8431b36fff21c437"
    assert result.ownership == "user_output"
    assert not list(tmp_path.glob("*.part"))


def test_parallel_publication_never_overwrites(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(publish_output, b"first", tmp_path, "image", "webp", managed=True)
        second = pool.submit(publish_output, b"second", tmp_path, "image", "webp", managed=True)
        results = (first.result(), second.result())

    assert {result.path.name for result in results} == {"image.webp", "image_002.webp"}
    assert {result.path.read_bytes() for result in results} == {b"first", b"second"}
    assert {result.ownership for result in results} == {"managed"}
    assert not list(tmp_path.glob("*.part"))
