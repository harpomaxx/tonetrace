"""The vectorized WAV decoder must match the pure-Python fallback bit-for-bit
(within float tolerance) across sample widths and channel counts, and read_wav
must round-trip known PCM values.
"""

from __future__ import annotations

import struct
import wave

import pytest

np = pytest.importorskip("numpy")

from notegrabber.analyzer import (
    _decode_pcm_frames_numpy,
    _decode_pcm_frames_python,
    read_wav,
)


def _pack(values_per_channel, sample_width: int, channels: int) -> bytes:
    """Pack interleaved integer PCM sample values into raw little-endian bytes."""

    out = bytearray()
    for frame in values_per_channel:
        for ch in range(channels):
            v = frame[ch]
            if sample_width == 1:
                out += bytes((v & 0xFF,))
            elif sample_width == 2:
                out += struct.pack("<h", v)
            elif sample_width == 3:
                out += (v & 0xFFFFFF).to_bytes(3, "little")
            else:
                out += struct.pack("<i", v)
    return bytes(out)


@pytest.mark.parametrize("sample_width", [1, 2, 3, 4])
@pytest.mark.parametrize("channels", [1, 2])
def test_numpy_path_matches_python_fallback(sample_width, channels):
    # A spread of representative integer sample values per channel.
    if sample_width == 1:
        frames = [(200, 50), (128, 128), (0, 255), (100, 160)]
    elif sample_width == 2:
        frames = [(30000, -30000), (0, 0), (-1, 1), (12345, -6789)]
    elif sample_width == 3:
        frames = [(8000000, -8000000), (0, 0), (-1, 1), (123456, -654321)]
    else:
        frames = [(2_000_000_000, -2_000_000_000), (0, 0), (-1, 1), (10, -10)]
    frames = [f[:channels] for f in frames]

    raw = _pack(frames, sample_width, channels)
    fast = _decode_pcm_frames_numpy(raw, channels, sample_width)
    slow = _decode_pcm_frames_python(raw, channels, sample_width)

    assert fast is not None
    assert len(fast) == len(slow) == len(frames)
    np.testing.assert_allclose(np.asarray(fast), np.asarray(slow), atol=1e-6)


def test_partial_trailing_frame_is_dropped():
    # 16-bit stereo needs 4 bytes/frame; append 3 stray bytes.
    raw = _pack([(1000, -1000), (500, -500)], sample_width=2, channels=2) + b"\x01\x02\x03"
    fast = _decode_pcm_frames_numpy(raw, channels=2, sample_width=2)
    assert len(fast) == 2  # the ragged tail is ignored, not decoded


def test_read_wav_round_trips_int16(tmp_path):
    path = tmp_path / "tone.wav"
    frames = [(16384, -16384), (0, 0), (32767, -32768)]
    raw = _pack(frames, sample_width=2, channels=2)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(raw)

    audio = read_wav(path)
    assert audio.sample_rate == 44100
    expected = [
        (16384 / 32768 + -16384 / 32768) / 2,
        0.0,
        (32767 / 32768 + -32768 / 32768) / 2,
    ]
    np.testing.assert_allclose(np.asarray(audio.samples), expected, atol=1e-4)
