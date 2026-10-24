"""Pinned upstream assets. Digests are publisher GitHub release metadata, not guessed hashes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    filename: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    version: str
    assets: tuple[Asset, ...]


SOURCE = "https://api.github.com/repos/thewh1teagle/kokoro-onnx/releases/tags/model-files-v1.1"
MODEL = ModelSpec(
    model_id="kokoro-v1.0-int8",
    version="1.0-int8-model-files-v1.1",
    assets=(
        Asset(
            "kokoro-v1.0.int8.onnx",
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
            "model-files-v1.1/kokoro-v1.0.int8.onnx",
            114119327,
            "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
        ),
        Asset(
            "voices-v1.0.bin",
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
            "model-files-v1.1/voices-v1.0.bin",
            28214398,
            "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        ),
    ),
)
