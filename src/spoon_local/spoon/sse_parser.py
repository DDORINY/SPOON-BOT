"""Bounded incremental UTF-8 SSE parser; explicit IDs never inherit."""

import codecs
from dataclasses import dataclass, field


class SSEParseError(Exception):
    def __init__(self):
        super().__init__("Invalid SSE frame")


@dataclass(frozen=True)
class SSEFrame:
    event: str
    data: str = field(repr=False)
    explicit_id: str | None = field(default=None, repr=False)
    retry_hint: int | None = None


class SSEParser:
    def __init__(self, max_frame_bytes: int = 65536):
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.max_frame_bytes = max_frame_bytes
        self.line: list[str] = []
        self.after_cr = False
        self.frame_size = 0
        self.data: list[str] = []
        self.event = ""
        self.explicit_id = None
        self.retry_hint = None
        self.last_event_id = ""

    def _line(self) -> SSEFrame | None:
        line = "".join(self.line)
        self.line.clear()
        if not line:
            frame = SSEFrame(self.event or "message", "\n".join(self.data), self.explicit_id, self.retry_hint) if self.data else None
            self.data, self.event = [], ""
            self.explicit_id, self.retry_hint = None, None
            self.frame_size = 0
            return frame
        if line.startswith(":"):
            return None
        name, separator, value = line.partition(":")
        if not separator:
            value = ""
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            self.data.append(value)
        elif name == "event":
            self.event = value
        elif name == "id" and "\x00" not in value:
            self.last_event_id = value
            self.explicit_id = value or None
        elif name == "retry" and value.isascii() and value.isdigit() and len(value) <= 9:
            self.retry_hint = int(value)
        return None

    def feed(self, chunk: bytes) -> list[SSEFrame]:
        try:
            text = self.decoder.decode(chunk)
        except UnicodeError:
            raise SSEParseError() from None
        frames = []
        for character in text:
            if self.after_cr:
                self.after_cr = False
                if character == "\n":
                    continue
            self.frame_size += len(character.encode("utf-8"))
            if self.frame_size > self.max_frame_bytes:
                raise SSEParseError()
            if character in "\r\n":
                frame = self._line()
                if frame is not None:
                    frames.append(frame)
                self.after_cr = character == "\r"
            else:
                self.line.append(character)
        return frames

    def finish(self) -> bool:
        """Return whether an incomplete frame was discarded; never dispatch at EOF."""
        try:
            remaining = self.decoder.decode(b"", final=True)
        except UnicodeError:
            raise SSEParseError() from None
        return bool(remaining or self.line or self.data or self.event or self.explicit_id)
