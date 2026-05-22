"""Pure functions for exFAT raw block operations — no I/O, no cache."""

import struct
from datetime import datetime, timezone


def _exfat_crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        crc ^= byte << 8
    for _ in range(8):
        crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    crc &= 0xFFFF
    return crc


def _exfat_entry_set_crc(entries: list[bytes]) -> int:
    crc = 0
    for entry in entries:
        crc = _exfat_crc16(entry[:2], crc)
        crc = _exfat_crc16(b'\x00\x00', crc)
        crc = _exfat_crc16(entry[4:], crc)
    return crc


def _exfat_encode_time(dt):
    utc = dt.replace(tzinfo=timezone.utc)
    year, month, day = utc.year, utc.month, utc.day
    hour, minute = utc.hour, utc.minute
    total_sec = int(utc.timestamp())
    sec = total_sec % 60
    ms = utc.microsecond // 1000
    date_word = ((year - 1980) << 9) | (month << 5) | day
    time_word = (hour << 11) | (minute << 5) | (sec // 2)
    time_ms = (sec % 2) * 100 + (ms // 10)
    return date_word, time_word, time_ms


def _exfat_decode_time(time_word: int, date_word: int, time_ms: int) -> datetime:
    year = ((date_word >> 9) & 0x7F) + 1980
    month = (date_word >> 5) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 11) & 0x1F
    minute = (time_word >> 5) & 0x3F
    sec_2block = time_word & 0x1F
    odd_second = 1 if time_ms >= 100 else 0
    second = sec_2block * 2 + odd_second
    millisecond = (time_ms % 100) * 10
    return datetime(year, month, day, hour, minute, second,
                    millisecond * 1000, tzinfo=timezone.utc)


def _exfat_entry_name(entries: list[bytes]) -> str:
    chars = []
    for e in entries:
        if e[0] == 0xC1:
            raw = e[2:32]
            for pos in range(0, 30, 2):
                cp = struct.unpack_from('<H', raw, pos)[0]
                if cp == 0:
                    break
                chars.append(chr(cp))
    return ''.join(chars)
