"""HDLC protocol implementation.

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
