from __future__ import annotations

import pytest

from mtp.media import Audio, File, Image, Video, coerce_images, coerce_audios, coerce_videos, coerce_files


class TestImage:
    def test_defaults(self):
        img = Image()
        assert img.url is None
        assert img.content is None
        assert isinstance(img.id, str)

    def test_from_bytes(self):
        img = Image(content=b"\x89PNG", format="png")
        assert img.get_content_bytes() == b"\x89PNG"
        b64 = img.to_base64()
        assert b64 is not None

    def test_from_dict_url(self):
        d = {"url": "http://example.com/img.png", "format": "png"}
        img = Image.from_dict(d)
        assert img.url == "http://example.com/img.png"
        assert img.format == "png"

    def test_from_dict_base64_content(self):
        import base64
        raw = b"image data"
        d = {"content": base64.b64encode(raw).decode()}
        img = Image.from_dict(d)
        assert img.content == raw

    def test_to_dict_roundtrip(self):
        img = Image(url="http://x.com/i.png", format="png", mime_type="image/png")
        d = img.to_dict()
        assert d["url"] == "http://x.com/i.png"
        img2 = Image.from_dict(d)
        assert img2.url == img.url

    def test_to_base64_none_when_no_content(self):
        img = Image(url="http://x.com/i.png")
        assert img.to_base64() is None


class TestAudio:
    def test_from_bytes(self):
        audio = Audio(content=b"RIFF", format="wav")
        assert audio.get_content_bytes() == b"RIFF"
        assert audio.to_base64() is not None

    def test_from_dict(self):
        d = {"url": "http://x.com/a.mp3", "format": "mp3", "transcript": "hello"}
        audio = Audio.from_dict(d)
        assert audio.transcript == "hello"
        assert audio.format == "mp3"

    def test_to_dict_roundtrip(self):
        audio = Audio(url="http://x.com/a.wav", format="wav")
        d = audio.to_dict()
        audio2 = Audio.from_dict(d)
        assert audio2.url == audio.url


class TestVideo:
    def test_from_bytes(self):
        vid = Video(content=b"mp4data", format="mp4")
        assert vid.get_content_bytes() == b"mp4data"

    def test_from_dict(self):
        d = {"url": "http://x.com/v.mp4"}
        vid = Video.from_dict(d)
        assert vid.url == "http://x.com/v.mp4"


class TestFile:
    def test_from_bytes(self):
        f = File(content=b"hello", filename="test.txt")
        assert f.get_content_bytes() == b"hello"
        assert f.to_base64() is not None

    def test_from_dict_string_content(self):
        d = {"content": "hello world", "filename": "test.txt"}
        f = File.from_dict(d)
        assert f.content == "hello world"

    def test_to_dict_with_string_content(self):
        f = File(content="hello", filename="test.txt")
        d = f.to_dict()
        assert d["content"] == "hello"

    def test_to_dict_with_bytes_content(self):
        f = File(content=b"\x00\x01\x02", filename="test.bin")
        d = f.to_dict()
        assert "content" in d


class TestCoerceFunctions:
    def test_coerce_images_none(self):
        assert coerce_images(None) is None

    def test_coerce_images_empty(self):
        assert coerce_images([]) is None

    def test_coerce_images_dict_list(self):
        result = coerce_images([{"url": "http://x.com/i.png"}])
        assert result is not None
        assert len(result) == 1
        assert isinstance(result[0], Image)

    def test_coerce_images_already_image(self):
        img = Image(url="http://x.com/i.png")
        result = coerce_images([img])
        assert result == [img]

    def test_coerce_audios_from_dict(self):
        result = coerce_audios([{"url": "http://x.com/a.wav"}])
        assert result is not None
        assert isinstance(result[0], Audio)

    def test_coerce_videos_from_dict(self):
        result = coerce_videos([{"url": "http://x.com/v.mp4"}])
        assert result is not None
        assert isinstance(result[0], Video)

    def test_coerce_files_from_dict(self):
        result = coerce_files([{"filename": "test.txt", "content": "hello"}])
        assert result is not None
        assert isinstance(result[0], File)
