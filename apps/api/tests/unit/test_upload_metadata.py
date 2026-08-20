from io import BytesIO

from fastapi import UploadFile
from starlette.datastructures import Headers
import pytest

from api.Validators.upload_metadata import (
    UploadMetadataValidator,
    ValidatedUploadMetadata,
)
from api.exceptions import (
    EmptyUploadError,
    MissingUploadFilenameError,
    UnsupportedDeclaredMediaTypeError,
    UnsupportedFileExtensionError,
    UploadSizeUnavailableError,
    UploadTooLargeError,
)


pytestmark = pytest.mark.unit
MAX_SIZE_BYTES = 100


def make_upload(
    *,
    filename: str | None = "recording.wav",
    size: int | None = 10,
    content_type: str | None = "audio/wav",
) -> UploadFile:
    headers = (
        Headers({"content-type": content_type})
        if content_type is not None
        else Headers()
    )
    return UploadFile(
        file=BytesIO(b"content must not be inspected"),
        size=size,
        filename=filename,
        headers=headers,
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_extension"),
    [
        ("recording.WAV", "audio/wav", "wav"),
        ("recording.mp3", "audio/mpeg", "mp3"),
        ("recording.ogg", "audio/ogg", "ogg"),
        ("recording.m4a", "audio/mp4", "m4a"),
        (r"C:\fakepath\recording.wav", "audio/x-wav", "wav"),
    ],
)
def test_validate_accepts_supported_file_extensions(
    filename: str,
    content_type: str,
    expected_extension: str,
) -> None:
    upload = make_upload(filename=filename, content_type=content_type)

    metadata = UploadMetadataValidator(MAX_SIZE_BYTES).validate(upload)

    assert metadata.extension == expected_extension


@pytest.mark.parametrize("filename", ["recording.flac", "recording", ".wav"])
def test_validate_rejects_unsupported_or_missing_file_extension(
    filename: str,
) -> None:
    upload = make_upload(filename=filename)

    with pytest.raises(UnsupportedFileExtensionError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(upload)


@pytest.mark.parametrize(
    ("filename", "content_type", "expected_content_type"),
    [
        ("recording.wav", " Audio/WAV ; codecs=1 ", "audio/wav"),
        ("recording.mp3", "audio/mp3", "audio/mp3"),
        ("recording.ogg", "application/ogg", "application/ogg"),
        ("recording.m4a", "audio/x-m4a", "audio/x-m4a"),
    ],
)
def test_validate_accepts_and_normalizes_supported_declared_mime_types(
    filename: str,
    content_type: str,
    expected_content_type: str,
) -> None:
    upload = make_upload(filename=filename, content_type=content_type)

    metadata = UploadMetadataValidator(MAX_SIZE_BYTES).validate(upload)

    assert metadata.declared_content_type == expected_content_type


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("recording.wav", "text/plain"),
        ("recording.wav", "audio/mpeg"),
        ("recording.wav", None),
        ("recording.wav", "   "),
    ],
)
def test_validate_rejects_unsupported_or_mismatched_declared_mime_type(
    filename: str,
    content_type: str | None,
) -> None:
    upload = make_upload(filename=filename, content_type=content_type)

    with pytest.raises(UnsupportedDeclaredMediaTypeError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(upload)


def test_validate_accepts_a_file_smaller_than_the_limit() -> None:
    metadata = UploadMetadataValidator(MAX_SIZE_BYTES).validate(make_upload(size=99))

    assert metadata.size_bytes == 99


def test_validate_accepts_a_file_exactly_at_the_limit() -> None:
    metadata = UploadMetadataValidator(MAX_SIZE_BYTES).validate(
        make_upload(size=MAX_SIZE_BYTES)
    )

    assert metadata.size_bytes == MAX_SIZE_BYTES


def test_validate_rejects_a_file_larger_than_the_limit() -> None:
    with pytest.raises(UploadTooLargeError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(make_upload(size=101))


def test_validate_rejects_an_empty_file() -> None:
    with pytest.raises(EmptyUploadError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(make_upload(size=0))


@pytest.mark.parametrize("size", [None, -1])
def test_validate_rejects_an_unavailable_or_invalid_size(size: int | None) -> None:
    with pytest.raises(UploadSizeUnavailableError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(make_upload(size=size))


@pytest.mark.parametrize("filename", [None, "", "   ", "/", ".."])
def test_validate_rejects_a_missing_or_unusable_filename(
    filename: str | None,
) -> None:
    with pytest.raises(MissingUploadFilenameError):
        UploadMetadataValidator(MAX_SIZE_BYTES).validate(make_upload(filename=filename))


def test_validate_does_not_read_the_uploaded_content() -> None:
    upload = make_upload()
    upload.file.seek(7)

    metadata = UploadMetadataValidator(MAX_SIZE_BYTES).validate(upload)

    assert metadata == ValidatedUploadMetadata(
        extension="wav",
        size_bytes=10,
        declared_content_type="audio/wav",
    )
    assert upload.file.tell() == 7


@pytest.mark.parametrize("max_size_bytes", [0, -1, True, 1.5, "100"])
def test_validator_rejects_an_invalid_maximum_size(max_size_bytes: object) -> None:
    with pytest.raises(ValueError):
        UploadMetadataValidator(max_size_bytes)  # type: ignore[arg-type]
