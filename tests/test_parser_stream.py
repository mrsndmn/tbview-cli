import os
import struct
import tempfile
from typing import List

import pytest

from tbview.parser import read_records, read_records_from_offset, test_crc32c as validate_crc32c
from tbview.tf_protobuf.event_pb2 import Event
from tbview.crc32c import masked_crc32c


def write_tfrecord_records(path: str, payloads: List[bytes]):
    # Always append to support incremental writes across calls
    with open(path, "ab") as f:
        for payload in payloads:
            length_bytes = struct.pack("Q", len(payload))
            length_crc = struct.pack("I", masked_crc32c(length_bytes))
            payload_crc = struct.pack("I", masked_crc32c(payload))
            f.write(length_bytes)
            f.write(length_crc)
            f.write(payload)
            f.write(payload_crc)


def make_event(step: int, tag: str, value: float) -> bytes:
    e = Event()
    e.step = step
    e.wall_time = 1000.0 + step
    v = e.summary.value.add()
    v.tag = tag
    v.simple_value = float(value)
    return e.SerializeToString()


def test_read_records_streams_all_events_and_stops_on_truncation():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.out.tfevents.test")
        payloads = [
            make_event(1, "loss", 0.9),
            make_event(2, "loss", 0.8),
            make_event(3, "acc", 0.1),
        ]
        write_tfrecord_records(path, payloads)
        # Truncate the file mid-header to simulate corruption after valid records
        with open(path, "ab") as f:
            f.write(b"\x00\x01\x02")

        steps = []
        for ev in read_records(path, warn=lambda m: None):
            steps.append(ev.step)

        assert steps == [1, 2, 3]


def test_read_records_from_offset_resumes_incrementally():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.out.tfevents.test")
        payloads = [
            make_event(10, "loss", 1.0),
            make_event(11, "loss", 0.9),
            make_event(12, "acc", 0.2),
        ]
        write_tfrecord_records(path, payloads)

        out = list(read_records_from_offset(path, 0, warn=lambda m: None))
        events, offsets = zip(*out)
        assert [e.step for e in events] == [10, 11, 12]
        # Resume from last offset should yield nothing new
        last = offsets[-1]
        out2 = list(read_records_from_offset(path, last, warn=lambda m: None))
        assert out2 == []

        # Append another record and ensure resume yields only the new one
        extra = make_event(13, "loss", 0.8)
        write_tfrecord_records(path, [extra])
        out3 = list(read_records_from_offset(path, last, warn=lambda m: None))
        events3, offsets3 = zip(*out3)
        assert [e.step for e in events3] == [13]
        assert offsets3[-1] > last


def test_test_crc32c_validation():
    data = b"hello world"
    good = struct.pack("I", masked_crc32c(data))
    bad = struct.pack("I", (masked_crc32c(data) + 1) & 0xFFFFFFFF)
    assert validate_crc32c(data, good) is True
    assert validate_crc32c(data, bad) is False


def test_read_records_stops_on_invalid_crc():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.out.tfevents.test")
        p1 = make_event(1, "loss", 0.5)
        p2 = make_event(2, "loss", 0.4)
        write_tfrecord_records(path, [p1, p2])
        # Corrupt the payload CRC of the second record
        with open(path, "r+b") as f:
            # skip first record: len(8)+crc(4)+payload+crc(4)
            off = 8 + 4 + len(p1) + 4
            f.seek(off + 8 + 4 + len(p2))
            good_crc = f.read(4)
            f.seek(off + 8 + 4 + len(p2))
            bad_crc = struct.pack("I", (struct.unpack("I", good_crc)[0] ^ 0xFFFFFFFF))
            f.write(bad_crc)

        steps = []
        for ev in read_records(path, warn=lambda m: None):
            steps.append(ev.step)
        # Should yield only the first record and stop on CRC error
        assert steps == [1]


