from __future__ import annotations

from pathlib import Path

from videocompress import inputs


def test_known_extension_match() -> None:
    assert inputs.is_known_video_extension(Path("clip.MTS"))
    assert inputs.is_known_video_extension(Path("movie.mkv"))
    assert not inputs.is_known_video_extension(Path("notes.txt"))


def test_collect_video_files_with_probe(tmp_path, monkeypatch) -> None:
    known = tmp_path / "clip.mkv"
    known.touch()
    unknown = tmp_path / "clip.weird"
    unknown.touch()

    monkeypatch.setattr(inputs, "has_video_stream", lambda p: p.suffix == ".weird")

    files = inputs.collect_video_files(tmp_path, recursive=False, probe_unknown=True)
    assert known in files
    assert unknown in files


def test_collect_video_files_without_probe(tmp_path, monkeypatch) -> None:
    known = tmp_path / "clip.mp4"
    known.touch()
    unknown = tmp_path / "clip.weird"
    unknown.touch()

    monkeypatch.setattr(inputs, "has_video_stream", lambda p: True)

    files = inputs.collect_video_files(tmp_path, recursive=False, probe_unknown=False)
    assert known in files
    assert unknown not in files
