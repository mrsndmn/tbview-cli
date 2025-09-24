import os
import struct

from tbview.crc32c import crc32c, masked_crc32c, u32


def test_crc32c_known_vectors():
    # Test against known CRC32C values (Castagnoli polynomial)
    # Source vectors appear in many CRC32C test suites
    vectors = [
        (b"", 0x00000000),
        (b"123456789", 0xE3069283),
        (b"The quick brown fox jumps over the lazy dog", 0x22620404),
        (b"\x00" * 32, 0x8A9136AA),
    ]
    for data, expected in vectors:
        assert u32(crc32c(data)) == expected


def test_masked_crc32c_roundtrip_properties():
    # Masking is a reversible transformation in TensorFlow TFRecord format:
    # unmask(mask(crc(x))) == crc(x). We don't implement unmask; instead verify
    # that masking preserves 32-bit range and is not identity on non-zero inputs.
    samples = [b"a", b"abc", os.urandom(64), b"\x00" * 10]
    for data in samples:
        base = u32(crc32c(data))
        masked = u32(masked_crc32c(data))
        assert 0 <= masked <= 0xFFFFFFFF
        if base != 0:
            assert masked != base


def test_u32_masks_to_32_bits():
    assert u32(0x1_0000_0000) == 0
    assert u32(-1) == 0xFFFFFFFF


