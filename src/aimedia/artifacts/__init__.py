"""Локальная подготовка artifact: конвертация байтов и публикация файла."""

from aimedia.artifacts.events import (
    ARTIFACT_CLEANUP_FAILED_EVENT,
    ARTIFACT_CONVERTED_EVENT,
    ARTIFACT_DOWNLOAD_FAILED_EVENT,
    ARTIFACT_DOWNLOAD_STARTED_EVENT,
    ARTIFACT_SAVED_EVENT,
    log_artifact_cleanup_failed,
    log_artifact_converted,
    log_artifact_download_failed,
    log_artifact_download_started,
    log_artifact_saved,
)
from aimedia.artifacts.image import (
    MAX_INPUT_BYTES,
    MAX_INPUT_PIXELS,
    ImageConversionError,
    ImageDecodeError,
    ImageTooLargeError,
    PreparedImage,
    UnsupportedImageInputError,
    UnsupportedImageModeError,
    convert_image,
    prepare_image,
)
from aimedia.artifacts.output import PublishedOutput, publish_output
from aimedia.artifacts.storage import PillowArtifactStorage

__all__ = [
    "ARTIFACT_CLEANUP_FAILED_EVENT",
    "ARTIFACT_CONVERTED_EVENT",
    "ARTIFACT_DOWNLOAD_FAILED_EVENT",
    "ARTIFACT_DOWNLOAD_STARTED_EVENT",
    "ARTIFACT_SAVED_EVENT",
    "MAX_INPUT_BYTES",
    "MAX_INPUT_PIXELS",
    "ImageConversionError",
    "ImageDecodeError",
    "ImageTooLargeError",
    "PreparedImage",
    "PublishedOutput",
    "UnsupportedImageInputError",
    "UnsupportedImageModeError",
    "PillowArtifactStorage",
    "convert_image",
    "log_artifact_cleanup_failed",
    "log_artifact_converted",
    "log_artifact_download_failed",
    "log_artifact_download_started",
    "log_artifact_saved",
    "prepare_image",
    "publish_output",
]
