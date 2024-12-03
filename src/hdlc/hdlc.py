"""Sensirion HDLC protocol implementation.

author: Miguel Ugsimar
"""

import logging
import struct
import asyncio
import abc

from typing import Tuple, Callable, Optional
from typing_extensions import Self

from .fcs import fcs16, FCS16_GOOD

#: default logger
_log = logging.getLogger(__file__)

HDLC_FLAG = 0x7E
HDLC_ESC = 0x7D
HDLC_XON = 0x31
HDLC_XOFF = 0x33


class ShdlcError(Exception):
    """Shdlc Exception baseclass."""


class FrameDecoder:

    def __init__(self):
        self._is_busy = False
        self._prev_byte = 0
        self._is_next_esc = False
        self._recv_buf = bytearray(0)
        self._max_bytes = 4

    def encode(self, data: bytes) -> bytes:
        """_summary_

        Args:
            data (bytes): _description_
        """
        encoded = []
        for byte in data:
            if byte in [HDLC_FLAG, HDLC_ESC, HDLC_XON, HDLC_XOFF]:
                encoded.extend(HDLC_ESC, byte ^ 0x20)
            else:
                encoded.append(byte)
        return bytes(encoded)

    def decode(self, data: bytes) -> Optional[bytes]:
        """_summary_

        Args:
            data (bytes): _description_

        Returns:
            Optional[bytes]: _description_
        """
        decoded = None

        for byte in data:
    
            if not self._is_busy and byte != HDLC_FLAG and self._prev_byte == HDLC_FLAG:
                self._is_busy = True
                self._is_next_esc = False
                self._recv_buf = bytearray(0)

            self._prev_byte = byte

            if self._is_busy:
                if byte == HDLC_FLAG: # end of frame, validate
                    self._is_busy = False

                    if (len(self._recv_buf) < self._max_bytes):
                        break

                    if FCS16_GOOD != fcs16(self._recv_buf):
                        break

                    decoded = self._recv_buf

                elif byte == HDLC_ESC:
                    if self._is_next_esc:
                        # invalid, we expect an escape byte
                        self._is_busy = False
                    self._is_next_esc = True

                else:
                    self._recv_buf.append((byte ^ 0x20) if self._is_next_esc else byte)

        return decoded

class HDLC(asyncio.Protocol):
    """HDLC Base Protocol."""

    def __init__(self):
        self._is_decoding = False
        self._decode_esc = False
        self._decode_recv_buf = bytearray(0)
        self._decode_prev_byte = 0

    def encode(self) -> None:
        pass

    def decode(self) -> None:
        pass

    def data_received(self, data):
        for byte in data:
            if (
                not self._decoding
                and byte != HDLC_FLAG
                and self._prev_byte == HDLC_FLAG
            ):
                self._is_busy = True
                self._is_next_esc = False
                self._recv_buf = bytearray(0)

    @abc.abstractmethod
    def send(self, data: bytes, reliable: bool = True) -> None:
        """_summary_

        Args:
            data (bytes): _description_
            reliable (bool, optional): _description_. Defaults to True.
        """
        raise NotImplementedError


class BalanceProtocol(HDLC):
    """Balance protocol. Balanced Configuration is generally required for Combined stations.
    This configuration consists of two combined stations that have equal and complementary
    responsibilities to enhance and emphasize the working and qualities of each other.
    """

    def __init__(
        self,
        *,
        loop: asyncio.BaseEventLoop | None = None,
        log: logging.Logger = _log,
    ):
        self._log = log if log else _log
        self._loop = loop if loop else asyncio.get_running_loop()
        self._transport: Optional[asyncio.Transport] = None
        self._send_retry_task: Optional[asyncio.Task] = None
        self._send_retry_count = 0
        self._send_count = 0
        self._recv_count = 0

    def connection_made(self, transport: asyncio.Transport) -> None:
        """Callback called by transport when a connection is successfully made.

        Args:
            transport (serial_asyncio): Transport layer instance.
        """
        if self._transport is None:
            self._log.debug("transport connected")
            self._transport = transport

    def connection_lost(self, exc: Exception) -> None:
        """Callback called by transport when connection is lost."""
        self._log.debug("connection lost")
        self._transport = None

    def send(self, data: bytes, reliable: bool = True) -> None:
        """_summary_

        Args:
            data (bytes): _description_
            reliable (bool, optional): _description_. Defaults to True.
        """
        pass


class ShdlcProtocol(asyncio.Protocol):
    """SHDLC async protocol."""

    def __init__(
        self,
        on_recv_callback: Callable[[bytes], Tuple[bytes, int] | None] = None,
        mode: str = "master",
        *,
        loop: asyncio.BaseEventLoop | None = None,
        log: logging.Logger = _log,
    ) -> None:
        super().__init__()
        self._transport: asyncio.Transport | None = None
        self._is_busy = False
        self._prev_byte = 0
        self._is_next_esc = False
        self._recv_buf = bytearray(0)
        self._mode = mode
        self._mosi_hdr = struct.Struct("<BBB")
        self._miso_hdr = struct.Struct("<BBBB")
        self._on_recv_callback = on_recv_callback
        self._log = log
        self._wait_response: asyncio.Future | None = None
        self._loop = loop if loop else asyncio.get_event_loop()
        self._wait_for_miso = (None, None)

    def connection_made(self, transport: asyncio.Transport) -> None:
        """Callback called by transport when a connection is successfully made.

        Args:
            transport (serial_asyncio): Transport layer instance.
        """
        if self._transport is None:
            self._log.debug("transport connected")
            self._is_busy = False
            self._transport = transport

    def connection_lost(self, exc: Exception) -> None:
        """Callback called by transport when connection is lost."""
        self._log.debug("connection lost")
        if self._wait_for_miso and not self._wait_response.done():
            self._wait_response.cancel(exc)
        self._transport = None

    def data_received(self, data: bytes) -> None:
        """Data received callback. This is called by the transport layer
        when a data is received."""
        for byte in data:
            if (
                not self._is_busy
                and byte != SHDLC_FLAG
                and self._prev_byte == SHDLC_FLAG
            ):
                self._is_busy = True
                self._is_next_esc = False
                self._recv_buf = bytearray(0)

            self._prev_byte = byte

            if self._is_busy:
                if byte == SHDLC_FLAG:
                    # end of frame, validate
                    length = len(self._recv_buf)
                    self._is_busy = False

                    # is length valid?
                    if (self._mode == "master" and length < self._miso_hdr.size) or (
                        self._mode == "slave" and (length < self._mosi_hdr.size)
                    ):
                        self._log.debug("frame to short: %s", length)
                        return

                    # checksum at end of buf
                    checksum = self._recv_buf.pop()
                    # is checksum valid?
                    calc_checksum = (~sum(self._recv_buf)) & 0xFF
                    if checksum != calc_checksum:
                        self._log.debug(
                            "Invalid checksum %x expect %x",
                            checksum,
                            calc_checksum,
                        )
                        return

                    if self._mode == "master":
                        self._master_receive()
                    elif self._mode == "slave":
                        self._slave_receive()

                elif byte == SHDLC_ESC:
                    if self._is_next_esc:
                        # invalid, we expect an escape byte
                        self._is_busy = False
                    self._is_next_esc = True

                else:
                    if self._is_next_esc:
                        self._is_next_esc = False
                        if byte in (0x5E, 0x5D, 0x31, 0x33):
                            byte ^= 1 << 5
                        else:
                            self._is_busy = False
                            self._log.debug("invalid escape byte 0x%x", byte)
                    self._recv_buf.append(byte)

    def _master_receive(self) -> None:
        (addr, cmd, status, length), data = (
            self._miso_hdr.unpack_from(self._recv_buf),
            self._recv_buf[self._miso_hdr.size :],
        )
        if (addr, cmd) != self._wait_for_miso:
            self._log.debug("frame not what expected addr %d, cmd %d", addr, cmd)
        self._wait_for_miso = (None, None)
        if length != (len(self._recv_buf) - self._miso_hdr.size):
            self._log.debug(
                "invalid frame length %d expected %d",
                len(self._recv_buf),
                length,
            )
        if self._wait_response:
            self._wait_response.set_result((status, data))

    def _slave_receive(self) -> None:
        addr, cmd, length = self._mosi_hdr.unpack_from(self._recv_buf)
        if self._on_recv_callback:
            rsp, status = self._on_recv_callback(
                addr, cmd, length, bytes(self._recv_buf[self._mosi_hdr.size :])
            )
            self._send(addr, cmd, status, rsp)

    def _hdlcify(self, data: bytes) -> None:
        encode = {
            b"\x7d": b"\x7d\x5d",
            b"\x7e": b"\x7d\x5e",
            b"\x11": b"\x7d\x31",
            b"\x13": b"\x7d\x33",
        }
        checksum = (~(sum(data))) & 0xFF
        data += checksum.to_bytes(1, "little")
        for k, v in encode.items():
            data = data.replace(k, v)
        return b"\x7e" + data + b"\x7e"

    def _send(
        self,
        address: int,
        cmd: int,
        status: int,
        data: bytes,
    ) -> None:
        length = len(data)
        if self._mode == "master":
            self._log.debug(
                "master sends - cmd: %x, len: %d, data: %s", cmd, length, data
            )
            header = self._mosi_hdr.pack(address, cmd, length)
        elif self._mode == "slave":
            header = self._miso_hdr.pack(address, cmd, status, length)
        data = self._hdlcify(header + data)
        if self._transport:
            self._transport.write(data)

    async def send(
        self,
        address: int,
        cmd: int,
        data: bytes | None = None,
        timeout: float | None = None,
    ) -> bytes:
        """Sends an SHDLC Frame.

        Args:
            address (int): Target address.
            cmd (int): Command to send.
            data (bytes | None, optional): Command data field. Defaults to None.
            timeout (float | None, optional): Timeout waiting for response. Defaults to None.

        Raises:
            TimeoutError: Raised when timeout waiting for response occur,
        """
        if self._mode == "slave":
            raise ShdlcError("Invalid state, mode is current slave mode")
        self._send(address, cmd, 0, data if data else b"")
        self._wait_for_miso = (address, cmd)
        # create a future and wait for a response
        self._wait_response = self._loop.create_future()
        await asyncio.wait_for(self._wait_response, timeout)
        rsp_status, rsp_data = self._wait_response.result()
        if rsp_status != 0:
            reason = SHDLC_STATUS_CODES.get(rsp_status, "Unknown error code")
            raise ShdlcError(f"response status error code: {rsp_status} | {reason}")
        return rsp_data
