import struct
from tbview.tf_protobuf.event_pb2 import Event
from tbview.tf_protobuf.summary_pb2 import Summary
from tbview.crc32c import masked_crc32c
from typing import Iterator, Tuple, Callable, Optional

def test_crc32c(data: bytes, crc_bytes: bytes) -> bool:
    """Validate masked CRC32C against provided bytes.

    Returns True when CRC matches, otherwise returns False.
    """
    if len(crc_bytes) != 4:
        return False
    expected_crc = struct.unpack('I', crc_bytes)[0]
    actual_crc = masked_crc32c(data)
    return expected_crc == actual_crc

def read_records(file_path, warn: Optional[Callable[[str], None]] = None):
    """Stream all `Event` protos from a TensorBoard TFRecord file.

    This function validates CRCs, guards against unreasonable record sizes,
    and stops gracefully when corruption is detected to avoid MemoryError.
    """
    MAX_RECORD_BYTES = 64 * 1024 * 1024  # 64MB safety cap
    def _warn(msg: str):
        if warn:
            warn(msg)
        else:
            print(msg)
    with open(file_path, 'rb') as f:
        while True:
            # Read length header (8 bytes) and its CRC (4 bytes)
            length_pos = f.tell()
            length_raw = f.read(8)
            if not length_raw:
                break
            length_crc = f.read(4)
            if len(length_raw) < 8 or len(length_crc) < 4:
                _warn('Warning: Truncated record header encountered, stopping read')
                break
            if not test_crc32c(length_raw, length_crc):
                _warn(f'Warning: Invalid length CRC at offset {length_pos}, stopping read')
                break
            length = struct.unpack('Q', length_raw)[0]
            if length <= 0 or length > MAX_RECORD_BYTES:
                _warn(f'Warning: Unreasonable record length {length} at offset {length_pos}, stopping read')
                break

            # Read payload and validate its CRC
            event_raw = f.read(length)
            if len(event_raw) != length:
                _warn('Warning: Truncated record payload encountered, stopping read')
                break
            payload_crc = f.read(4)
            if len(payload_crc) < 4 or not test_crc32c(event_raw, payload_crc):
                _warn('Warning: Invalid payload CRC, stopping read')
                break

            # Parse event proto
            try:
                event = Event()
                event.ParseFromString(event_raw)
            except Exception as e:
                _warn(f'Warning: Failed to parse Event proto: {e}. Stopping read')
                break
            yield event


def read_records_from_offset(file_path: str, start_offset: int = 0, warn: Optional[Callable[[str], None]] = None, sample_rate: int = 1) -> Iterator[Tuple[Event, int]]:
    """Read tensorboard events starting from a file offset.

    Yields tuples of (Event, end_offset) where end_offset is the file position
    immediately after reading the event and its CRC trailer. This enables
    incremental reading by resuming from the last offset next time.

    Args:
        file_path: Path to the tensorboard event file
        start_offset: Byte offset to start reading from
        warn: Optional warning callback function
        sample_rate: Only parse and yield every Nth event (default: 1 = all events)
                    Higher values speed up loading but provide less data
    """
    MAX_RECORD_BYTES = 64 * 1024 * 1024  # 64MB safety cap
    def _warn(msg: str):
        if warn:
            warn(msg)
        else:
            print(msg)
    with open(file_path, 'rb') as f:
        if start_offset:
            f.seek(start_offset)
        event_counter = 0
        while True:
            header_pos = f.tell()
            length_raw = f.read(8)
            if not length_raw:
                break
            length_crc = f.read(4)
            if len(length_raw) < 8 or len(length_crc) < 4:
                _warn('Warning: Truncated record header encountered, stopping read')
                break
            if not test_crc32c(length_raw, length_crc):
                _warn(f'Warning: Invalid length CRC at offset {header_pos}, stopping read')
                break
            length = struct.unpack('Q', length_raw)[0]
            if length <= 0 or length > MAX_RECORD_BYTES:
                _warn(f'Warning: Unreasonable record length {length} at offset {header_pos}, stopping read')
                break

            event_raw = f.read(length)
            if len(event_raw) != length:
                _warn('Warning: Truncated record payload encountered, stopping read')
                break
            payload_crc = f.read(4)
            if len(payload_crc) < 4 or not test_crc32c(event_raw, payload_crc):
                _warn('Warning: Invalid payload CRC, stopping read')
                break

            event_counter += 1
            end_offset = f.tell()

            # Skip parsing if not matching sample rate
            if sample_rate > 1 and event_counter % sample_rate != 0:
                # Yield None to indicate skipped event but update offset
                yield None, end_offset
                continue

            try:
                event = Event()
                event.ParseFromString(event_raw)
            except Exception as e:
                _warn(f'Warning: Failed to parse Event proto: {e}. Stopping read')
                break
            yield event, end_offset
