"""Файловый adapter `ArtifactStorage`: метаданные фактических байтов и пути (E05, C08c1).

Проверяется не намерение, а результат: сохранённый файл действительно декодируется,
`mime_type`/`size_bytes`/`sha256` взяты из опубликованных байтов, managed-путь
относителен data-root, пользовательский путь абсолютен и не копируется, а подсказка
`source_mime_type` не подменяет фактический формат. Изображения собирает реальный
Pillow, все файлы живут в `tmp_path`; сеть и пользовательский data-root не
затрагиваются.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from aimedia.artifacts import PillowArtifactStorage
from aimedia.domain import Artifact, ArtifactKind, ArtifactRole, FinalFormat

WHITE = (255, 255, 255)


def _encode(image: Image.Image, pillow_format: str, **options: object) -> bytes:
    buffer = BytesIO()
    image.save(buffer, pillow_format, **options)
    return buffer.getvalue()


def _rgb_png(width: int = 4, height: int = 3, color: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    return _encode(Image.new("RGB", (width, height), color), "PNG")


def _rgba_png(width: int = 4, height: int = 3) -> bytes:
    image = Image.new("RGBA", (width, height))
    image.putdata(
        [
            (x * 40 % 256, y * 60 % 256, (x + y) * 30 % 256, 255 - (x * 10 % 200))
            for y in range(height)
            for x in range(width)
        ]
    )
    return _encode(image, "PNG")


def _transparent_png() -> bytes:
    return _encode(Image.new("RGBA", (2, 1), (10, 20, 30, 0)), "PNG")


def _storage(tmp_path: Path) -> PillowArtifactStorage:
    return PillowArtifactStorage(data_root=tmp_path)


def _decode(path: Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image


def test_managed_final_reports_metadata_of_published_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    source = _rgb_png()

    artifact = storage.save(
        job_id=7, content=source, final_format=FinalFormat.PNG, base_name="scientist"
    )

    assert artifact.kind is ArtifactKind.IMAGE
    assert artifact.role is ArtifactRole.FINAL
    assert artifact.local_path == Path("outputs/7/scientist.png")
    path = storage.resolve_path(artifact)
    assert path == tmp_path / "outputs" / "7" / "scientist.png"
    assert storage.exists(artifact) is True
    assert path.read_bytes() == source

    decoded = _decode(path)
    assert decoded.format == "PNG"
    assert artifact.mime_type == "image/png"
    assert artifact.size_bytes == len(source)
    assert artifact.sha256 == hashlib.sha256(source).hexdigest()
    assert (artifact.metadata["width"], artifact.metadata["height"]) == decoded.size
    assert artifact.metadata["ownership"] == "managed"
    assert artifact.metadata["converted"] is False
    assert artifact.metadata["alpha_flattened"] is False
    assert artifact.metadata["source_format"] == "png"
    assert artifact.metadata["final_format"] == "png"


def test_default_basename_is_deterministic(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    artifact = storage.save(job_id=12, content=_rgb_png(), final_format=FinalFormat.PNG)

    assert artifact.local_path == Path("outputs/12/result.png")


def test_transparent_png_to_webp_keeps_alpha_and_hash_from_final_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    artifact = storage.save(job_id=1, content=_rgba_png(), final_format=FinalFormat.WEBP)

    assert artifact.local_path == Path("outputs/1/result.webp")
    data = storage.resolve_path(artifact).read_bytes()
    decoded = _decode(storage.resolve_path(artifact))
    assert decoded.format == "WEBP"
    assert "A" in decoded.getbands()
    assert artifact.mime_type == "image/webp"
    assert artifact.metadata["converted"] is True
    assert artifact.metadata["alpha_flattened"] is False
    # Hash описывает конечный WebP, а не исходный PNG.
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert artifact.size_bytes == len(data)


def test_transparent_png_to_jpeg_flattens_on_white_and_reports_alpha_loss(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    artifact = storage.save(job_id=2, content=_transparent_png(), final_format=FinalFormat.JPEG)

    assert artifact.local_path == Path("outputs/2/result.jpeg")
    decoded = _decode(storage.resolve_path(artifact))
    assert decoded.format == "JPEG"
    assert "A" not in decoded.getbands()
    assert decoded.getpixel((0, 0)) == WHITE
    assert artifact.mime_type == "image/jpeg"
    assert artifact.metadata["alpha_flattened"] is True


def test_source_mime_hint_never_overrides_actual_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    source = _rgb_png()

    preserved = storage.save(
        job_id=1, content=source, final_format=None, source_mime_type="image/jpeg"
    )
    converted = storage.save(
        job_id=2, content=source, final_format=FinalFormat.WEBP, source_mime_type="image/png"
    )

    assert preserved.mime_type == "image/png"
    assert preserved.local_path == Path("outputs/1/result.png")
    assert converted.mime_type == "image/webp"
    assert converted.local_path == Path("outputs/2/result.webp")


def test_original_role_preserves_source_bytes_and_ignores_final_format(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    source = _rgba_png()

    artifact = storage.save(
        job_id=4,
        content=source,
        role=ArtifactRole.ORIGINAL,
        final_format=FinalFormat.WEBP,
        base_name="original",
    )

    assert artifact.role is ArtifactRole.ORIGINAL
    assert artifact.local_path == Path("outputs/4/original.png")
    assert storage.resolve_path(artifact).read_bytes() == source
    assert artifact.mime_type == "image/png"
    assert artifact.metadata["converted"] is False


def test_user_output_dir_stores_absolute_path_without_hidden_copy(tmp_path: Path) -> None:
    data_root = tmp_path / "appdata"
    user_dir = tmp_path / "user-output"
    user_dir.mkdir()
    storage = PillowArtifactStorage(data_root=data_root)

    artifact = storage.save(
        job_id=3,
        content=_rgb_png(),
        final_format=FinalFormat.PNG,
        output_dir=user_dir,
        base_name="export",
    )

    assert artifact.local_path == user_dir / "export.png"
    assert artifact.local_path is not None and artifact.local_path.is_absolute()
    assert artifact.metadata["ownership"] == "user_output"
    assert storage.resolve_path(artifact) == user_dir / "export.png"
    assert set(user_dir.iterdir()) == {user_dir / "export.png"}
    # Скрытой второй копии в managed-каталоге нет.
    assert not data_root.exists()


def test_missing_user_output_dir_is_rejected_without_creating_it(tmp_path: Path) -> None:
    storage = PillowArtifactStorage(data_root=tmp_path / "appdata")
    missing = tmp_path / "missing"

    with pytest.raises(ValueError):
        storage.save(job_id=1, content=_rgb_png(), final_format=FinalFormat.PNG, output_dir=missing)

    assert not missing.exists()


def test_collision_gets_new_path_without_overwriting(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first_source = _rgb_png(color=(1, 2, 3))
    second_source = _rgb_png(color=(200, 100, 50))

    first = storage.save(
        job_id=5, content=first_source, final_format=FinalFormat.PNG, base_name="dup"
    )
    second = storage.save(
        job_id=5, content=second_source, final_format=FinalFormat.PNG, base_name="dup"
    )

    assert first.local_path == Path("outputs/5/dup.png")
    assert second.local_path == Path("outputs/5/dup_002.png")
    assert storage.resolve_path(first).read_bytes() == first_source
    assert storage.resolve_path(second).read_bytes() == second_source


def test_cleanup_failure_signal_is_flag_without_raw_temp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_unlink = Path.unlink

    def refuse_temp_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.suffix == ".part":
            raise PermissionError("temporary cleanup denied")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_temp_unlink)
    storage = _storage(tmp_path)

    artifact = storage.save(job_id=6, content=_rgb_png(), final_format=FinalFormat.PNG)

    assert artifact.metadata["temp_cleanup_failed"] is True
    assert all(".part" not in str(value) for value in artifact.metadata.values())
    # Опубликованный результат всё равно существует.
    assert storage.exists(artifact) is True


def test_exists_false_after_file_removal_but_path_is_retained(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    artifact = storage.save(job_id=8, content=_rgb_png(), final_format=FinalFormat.PNG)

    storage.resolve_path(artifact).unlink()

    assert storage.exists(artifact) is False
    assert artifact.local_path == Path("outputs/8/result.png")


def test_unsafe_base_name_is_rejected_without_publishing(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ValueError, match="Unsafe output basename"):
        storage.save(
            job_id=9, content=_rgb_png(), final_format=FinalFormat.PNG, base_name="../escape"
        )

    # Публикации не было: managed-каталог создан, но не содержит файла.
    managed_dir = tmp_path / "outputs" / "9"
    assert not managed_dir.exists() or list(managed_dir.iterdir()) == []


def test_relative_traversal_is_rejected(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    escaping = Artifact(
        kind=ArtifactKind.IMAGE,
        role=ArtifactRole.FINAL,
        local_path=Path("../escape.png"),
    )

    with pytest.raises(ValueError, match="data-root"):
        storage.resolve_path(escaping)
    with pytest.raises(ValueError, match="data-root"):
        storage.exists(escaping)


@pytest.mark.parametrize(
    "relative_path",
    [
        "outputs/1/../../other.png",
        "outputs/1/../2/result.png",
        "other.png",
        "cache/1/result.png",
        "outputs/0/result.png",
        "outputs/-1/result.png",
        "outputs/01/result.png",
        "outputs/1/subdir/result.png",
    ],
)
def test_forged_managed_path_is_rejected(tmp_path: Path, relative_path: str) -> None:
    storage = _storage(tmp_path)
    artifact = Artifact(kind=ArtifactKind.IMAGE, local_path=Path(relative_path))

    with pytest.raises(ValueError):
        storage.resolve_path(artifact)
    with pytest.raises(ValueError):
        storage.exists(artifact)


@pytest.mark.parametrize("job_id", ["../../outside", True, False, 0, -1])
def test_invalid_job_id_does_not_publish_or_create_directory(
    tmp_path: Path, job_id: object
) -> None:
    data_root = tmp_path / "appdata"
    storage = _storage(data_root)
    outside = tmp_path / "outside" / "result.png"

    try:
        with pytest.raises(ValueError, match="job_id"):
            storage.save(job_id=job_id, content=_rgb_png(), final_format=FinalFormat.PNG)  # type: ignore[arg-type]
        assert not outside.exists()
        assert not (data_root / "outputs").exists()
    finally:
        # Also clean the out-of-root file produced by a vulnerable implementation.
        outside.unlink(missing_ok=True)


def test_managed_symlinked_directory_and_file_are_rejected(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "result.png").write_bytes(_rgb_png())
    managed_dir = tmp_path / "outputs" / "1"
    managed_dir.parent.mkdir()
    try:
        managed_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")

    artifact = Artifact(kind=ArtifactKind.IMAGE, local_path=Path("outputs/1/result.png"))
    for operation in (storage.exists, storage.resolve_path):
        with pytest.raises(ValueError, match="[Ss]ymlink|junction"):
            operation(artifact)

    managed_dir.unlink()
    managed_dir.mkdir()
    (managed_dir / "result.png").symlink_to(real_dir / "result.png")
    for operation in (storage.exists, storage.resolve_path):
        with pytest.raises(ValueError, match="[Ss]ymlink|junction"):
            operation(artifact)


def test_managed_data_root_symlink_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    artifact = _storage(real_root).save(job_id=1, content=_rgb_png())
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")

    storage = _storage(linked_root)
    for operation in (storage.exists, storage.resolve_path):
        with pytest.raises(ValueError, match="[Ss]ymlink|junction"):
            operation(artifact)


@pytest.mark.parametrize("redirect", ["symlink", "junction"])
@pytest.mark.parametrize("component", ["ancestor", "data_root", "outputs", "managed_dir"])
def test_save_rejects_managed_directory_redirect_before_mkdir(
    tmp_path: Path, component: str, redirect: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_bytes(b"unchanged")
    data_root = tmp_path / "appdata"
    if component == "ancestor":
        link = tmp_path / "linked-parent"
        data_root = link / "appdata"
    elif component == "data_root":
        link = data_root
    elif component == "outputs":
        data_root.mkdir()
        link = data_root / "outputs"
    else:
        (data_root / "outputs").mkdir(parents=True)
        link = data_root / "outputs" / "1"

    if redirect == "junction":
        if sys.platform != "win32":
            pytest.skip("Junctions are Windows-only")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            pytest.skip("Directory junctions unavailable")
        assert link.is_junction()
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Directory symlinks unavailable: {exc}")

    storage = _storage(data_root)
    with pytest.raises(ValueError, match="[Ss]ymlink|junction"):
        storage.save(job_id=1, content=_rgb_png(), final_format=FinalFormat.PNG)

    # A rejected save returns no Artifact and leaves even directories outside untouched.
    assert list(outside.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"unchanged"


def test_artifact_without_local_path_is_rejected(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    artifact = Artifact(kind=ArtifactKind.IMAGE, role=ArtifactRole.FINAL)

    with pytest.raises(ValueError):
        storage.resolve_path(artifact)
