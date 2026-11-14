"""Bounded, read-only A2S_INFO metadata query for an owned local test server.

The caller must first establish that its own child process owns the query port,
then compare the reply's name to the unique hostname it configured. A response
alone establishes neither process ownership nor player authentication or spawn.
There is no host parameter: this module connects only to IPv4 127.0.0.1.

Protocol references:
https://developer.valvesoftware.com/wiki/Server_queries
https://steamcommunity.com/discussions/forum/14/2974028351344359625/
The second source is Valve developer Fletcher Dunn's announcement confirming
that A2S_INFO may require a four-byte challenge appended to the original query.
No A2S_PLAYER, A2S_RULES, RCon or game authentication messages are sent.
"""
from __future__ import annotations

import errno
import math
import socket
import struct
import time
from typing import Callable

LOOPBACK = "127.0.0.1"
MAX_DATAGRAM = 8192
MAX_TIMEOUT = 5.0
MAX_CHALLENGE_RETRIES = 2
SINGLE_HEADER = b"\xff\xff\xff\xff"
SPLIT_HEADER = b"\xfe\xff\xff\xff"
INFO_REQUEST = SINGLE_HEADER + b"\x54Source Engine Query\x00"
INFO_TYPE = 0x49
CHALLENGE_TYPE = 0x41
KNOWN_EDF_FLAGS = 0xF1


class QueryDataError(ValueError):
    def __init__(self, code: str, status: str = "MALFORMED"):
        super().__init__(code)
        self.code, self.status = code, status


def _message_type(data: bytes) -> int:
    if not isinstance(data, bytes):
        raise QueryDataError("INVALID_PACKET_TYPE")
    # recv() reads at most this many bytes. Reject an exact-limit packet too,
    # because some operating systems silently truncate an oversized datagram.
    if len(data) >= MAX_DATAGRAM:
        raise QueryDataError("DATAGRAM_SIZE_LIMIT")
    if len(data) < 5:
        raise QueryDataError("TRUNCATED_HEADER")
    if data[:4] == SPLIT_HEADER:
        raise QueryDataError("SPLIT_OR_COMPRESSED_RESPONSE", "UNSUPPORTED_SPLIT")
    if data[:4] != SINGLE_HEADER:
        raise QueryDataError("INVALID_HEADER")
    return data[4]


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 5

    def read(self, length: int) -> bytes:
        if self.pos + length > len(self.data):
            raise QueryDataError("TRUNCATED_FIELD")
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        return value

    def byte(self) -> int:
        return self.read(1)[0]

    def ushort(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def uint64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def string(self, limit: int = 256) -> str:
        end = self.data.find(b"\x00", self.pos, min(len(self.data), self.pos + limit + 1))
        if end < 0:
            raise QueryDataError("STRING_SIZE_LIMIT" if len(self.data) - self.pos > limit else "TRUNCATED_STRING")
        raw = self.data[self.pos:end]
        self.pos = end + 1
        try:
            value = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise QueryDataError("INVALID_UTF8") from None
        if len(value) > limit:
            raise QueryDataError("STRING_SIZE_LIMIT")
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            raise QueryDataError("CONTROL_CHARACTER")
        return value


def _result(status: str, code: str, info: dict | None = None) -> dict:
    return {"status": status, "code": code, "info": info}


def parse_info_response(data: bytes) -> dict:
    """Parse a single standard INFO reply; split/compressed packets are rejected.

    Every numeric field is little-endian and bounds checked. Strings are bounded
    UTF-8. Metadata is self-reported, not game-build or platform verification.
    appid16 is the raw 16-bit wire field, never treated as full Steam app 221100.
    Optional game_id_raw/steam_id_raw are opaque unsigned 64-bit wire values.
    """
    try:
        if _message_type(data) != INFO_TYPE:
            raise QueryDataError("UNEXPECTED_RESPONSE_TYPE")
        reader = _Reader(data)
        info = {
            "protocol": reader.byte(),
            "name": reader.string(),
            "map": reader.string(),
            "folder": reader.string(),
            "game": reader.string(512),
            "appid16": reader.ushort(),
            "players": reader.byte(),
            "max_players": reader.byte(),
            "bots": reader.byte(),
            "server_type": chr(reader.byte()),
            "environment": chr(reader.byte()),
            "visibility": reader.byte(),
            "vac": reader.byte(),
        }
        if info["server_type"] not in ("d", "l", "p"):
            raise QueryDataError("INVALID_SERVER_TYPE")
        if info["environment"] not in ("l", "w", "m", "o"):
            raise QueryDataError("INVALID_ENVIRONMENT")
        if info["visibility"] not in (0, 1) or info["vac"] not in (0, 1):
            raise QueryDataError("INVALID_BOOLEAN_FIELD")
        # The documented The Ship extension precedes version, unlike EDF.
        if info["appid16"] == 2400:
            info["ship_mode"] = reader.byte()
            info["ship_witnesses"] = reader.byte()
            info["ship_duration"] = reader.byte()
        info["version"] = reader.string()
        if reader.pos < len(data):
            edf = reader.byte()
            info["edf"] = edf
            if edf & ~KNOWN_EDF_FLAGS:
                raise QueryDataError("UNKNOWN_EDF_FLAGS")
            if edf & 0x80:
                info["port"] = reader.ushort()
            if edf & 0x10:
                info["steam_id_raw"] = reader.uint64()
            if edf & 0x40:
                info["spectator_port"] = reader.ushort()
                info["spectator_name"] = reader.string()
            if edf & 0x20:
                info["keywords"] = reader.string(512)
            if edf & 0x01:
                info["game_id_raw"] = reader.uint64()
        if reader.pos != len(data):
            raise QueryDataError("UNEXPECTED_TRAILING_BYTES")
        return _result("RESPONSE", "INFO_PARSED", info)
    except QueryDataError as error:
        return _result(error.status, error.code)


def query_info(port: int, timeout: float = 1.0, *,
               socket_factory: Callable | None = None,
               clock: Callable[[], float] | None = None) -> dict:
    """Query only 127.0.0.1:port within one deadline of at most five seconds.

    The initial request plus at most two challenge retries are sent. Connected
    UDP filters responses by this exact local peer. No password is transmitted.
    Tests may inject socket_factory and clock; ordinary use leaves both unset.
    elapsed_ms measures this whole exchange, including challenge negotiation;
    it is deliberately not labelled ping or a game-server round-trip latency.
    """
    now = clock or time.monotonic
    started = now()
    attempts = challenges = 0
    sock = None

    def finish(status: str, code: str, info: dict | None = None) -> dict:
        result = _result(status, code, info)
        result.update(attempts=attempts, challenges=challenges,
                      elapsed_ms=round(max(0.0, now() - started) * 1000, 1))
        return result

    if type(port) is not int or not 1 <= port <= 65535:
        return finish("UNAVAILABLE", "INVALID_PORT")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT or not math.isfinite(timeout):
        return finish("UNAVAILABLE", "INVALID_TIMEOUT")
    deadline = started + float(timeout)

    def remaining() -> float:
        value = deadline - now()
        if value <= 0:
            raise socket.timeout()
        return value

    try:
        sock = (socket_factory or socket.socket)(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(remaining())
        sock.connect((LOOPBACK, port))
        request = INFO_REQUEST
        while True:
            sock.settimeout(remaining())
            attempts += 1
            if sock.send(request) != len(request):
                return finish("UNAVAILABLE", "SEND_INCOMPLETE")
            sock.settimeout(remaining())
            packet = sock.recv(MAX_DATAGRAM)
            # A reply arriving after the overall deadline cannot extend it.
            remaining()
            packet_type = _message_type(packet)
            if packet_type != CHALLENGE_TYPE:
                parsed = parse_info_response(packet)
                return finish(parsed["status"], parsed["code"], parsed["info"])
            if len(packet) != 9:
                return finish("MALFORMED", "INVALID_CHALLENGE_SIZE")
            if challenges >= MAX_CHALLENGE_RETRIES:
                return finish("MALFORMED", "CHALLENGE_RETRY_LIMIT")
            challenges += 1
            # The nonce is opaque bytes: never reinterpret its signedness,
            # serialize it to text, or accumulate previous challenges.
            request = INFO_REQUEST + packet[5:9]
    except QueryDataError as error:
        return finish(error.status, error.code)
    except (socket.timeout, TimeoutError):
        return finish("TIMEOUT", "DEADLINE_OR_SOCKET_TIMEOUT")
    except OSError as error:
        number = getattr(error, "winerror", None) or error.errno
        if number in (errno.EMSGSIZE, 10040):
            return finish("MALFORMED", "DATAGRAM_SIZE_LIMIT")
        if number in (errno.ECONNREFUSED, errno.ECONNRESET, 10054, 10061):
            return finish("UNAVAILABLE", "NO_LOCAL_QUERY_LISTENER")
        return finish("UNAVAILABLE", "SOCKET_ERROR")
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
