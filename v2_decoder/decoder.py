"""V2 Market Decoder - decodes raw Solana account bytes into structured data."""

import base64
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import requests


class MarketStatus(Enum):
    UNKNOWN = 0
    ACTIVE = 1
    RESOLVED_YES = 2
    RESOLVED_NO = 3
    CANCELLED = 4


@dataclass
class PoolInfo:
    yes_pool: float = 0.0
    no_pool: float = 0.0
    weighted_yes_pool: float = 0.0
    weighted_no_pool: float = 0.0


@dataclass
class DecodedMarket:
    account_pubkey: str = ""
    market_id: str = ""
    title: str = ""
    status: MarketStatus = MarketStatus.UNKNOWN
    creation_time: float = 0.0
    estimated_end_time: float = 0.0
    resolved_time: float = 0.0
    pool: PoolInfo = field(default_factory=PoolInfo)
    total_yes_votes: float = 0.0
    total_no_votes: float = 0.0
    participant_count: int = 0
    creator_pubkey: str = ""
    cover_image_url: str = ""
    market_link: str = ""
    raw_data: bytes = b""
    decoded_offsets: dict[str, int] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    unknown_fields: list[tuple[int, int, bytes]] = field(default_factory=list)
    decode_warnings: list[str] = field(default_factory=list)


@dataclass
class FieldSpec:
    name: str
    offset: int
    size: int
    format: str
    description: str
    confidence: float = 0.0
    required: bool = True
    category: str = "unknown"


class V2MarketDecoder:
    B4_PROGRAM_ID = "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH"
    BINARY_LAYOUT_VERSION = 1
    KNOWN_FIELDS: list[FieldSpec] = []
    MIN_CONFIDENCE = 0.95

    def __init__(self, strict: bool = False, confidence_threshold: float = 0.95):
        self.strict = strict
        self.confidence_threshold = confidence_threshold
        self._layout = self._build_layout()

    def _build_layout(self) -> list[FieldSpec]:
        return [
            FieldSpec("discriminator", 0, 8, "8s", "Account type discriminator", 0.0, True, "system"),
            FieldSpec("version", 8, 4, "<I", "Schema version", 0.0, True, "system"),
            FieldSpec("market_id", 12, 32, "32s", "Market ID (pubkey)", 0.0, True, "identity"),
            FieldSpec("creator", 44, 32, "32s", "Creator pubkey", 0.0, True, "identity"),
            FieldSpec("status_raw", 76, 4, "<I", "Market status enum", 0.0, True, "state"),
            FieldSpec("title_len", 80, 4, "<I", "Title string length", 0.0, True, "metadata"),
            FieldSpec("title", 84, 0, "str", "Market title (dynamic)", 0.0, True, "metadata"),
            FieldSpec("cover_url_len", 84, 4, "<I", "Cover URL length", 0.0, False, "metadata"),
            FieldSpec("cover_url", 88, 0, "str", "Cover image URL (dynamic)", 0.0, False, "metadata"),
            FieldSpec("link_len", 88, 4, "<I", "Market link length", 0.0, False, "metadata"),
            FieldSpec("link", 92, 0, "str", "Market link (dynamic)", 0.0, False, "metadata"),
            FieldSpec("creation_ts", 0, 8, "<Q", "Creation timestamp (unix)", 0.0, True, "time"),
            FieldSpec("end_ts", 8, 8, "<Q", "Estimated end timestamp", 0.0, True, "time"),
            FieldSpec("resolved_ts", 16, 8, "<Q", "Resolved timestamp", 0.0, False, "time"),
            FieldSpec("yes_pool", 24, 8, "<d", "YES pool amount", 0.0, True, "pool"),
            FieldSpec("no_pool", 32, 8, "<d", "NO pool amount", 0.0, True, "pool"),
            FieldSpec("weighted_yes", 40, 8, "<d", "Weighted YES pool", 0.0, True, "pool"),
            FieldSpec("weighted_no", 48, 8, "<d", "Weighted NO pool", 0.0, True, "pool"),
            FieldSpec("total_yes_votes", 56, 8, "<d", "Total YES votes", 0.0, True, "pool"),
            FieldSpec("total_no_votes", 64, 8, "<d", "Total NO votes", 0.0, True, "pool"),
            FieldSpec("participant_count", 72, 4, "<I", "Number of participants", 0.0, True, "state"),
        ]

    def decode(self, account_pubkey: str, raw_data: bytes) -> DecodedMarket:
        market = DecodedMarket(
            account_pubkey=account_pubkey,
            raw_data=raw_data,
        )

        if len(raw_data) < 8:
            market.decode_warnings.append(f"Data too short: {len(raw_data)} bytes")
            return market

        self._decode_discriminator(market, raw_data)
        self._decode_identity(market, raw_data)
        self._decode_state(market, raw_data)
        self._decode_metadata(market, raw_data)
        self._decode_pool(market, raw_data)
        self._decode_time(market, raw_data)

        if self.strict:
            self._validate_strict(market)

        return market

    def _decode_discriminator(self, market: DecodedMarket, data: bytes):
        disc = data[0:8]
        market.decoded_offsets["discriminator"] = 0
        market.confidence["discriminator"] = 0.6

    def _decode_identity(self, market: DecodedMarket, data: bytes):
        if len(data) >= 44:
            market.market_id = base64.b32encode(data[12:44]).decode()
            market.decoded_offsets["market_id"] = 12
            market.confidence["market_id"] = 0.5

            market.creator_pubkey = base64.b32encode(data[44:76]).decode()
            market.decoded_offsets["creator"] = 44
            market.confidence["creator"] = 0.5

    def _decode_state(self, market: DecodedMarket, data: bytes):
        if len(data) >= 80:
            status_val = struct.unpack_from("<I", data, 76)[0]
            try:
                market.status = MarketStatus(status_val)
            except ValueError:
                market.status = MarketStatus.UNKNOWN
            market.decoded_offsets["status_raw"] = 76
            market.confidence["status_raw"] = 0.5

    def _decode_metadata(self, market: DecodedMarket, data: bytes):
        offset = 80
        if len(data) >= offset + 4:
            title_len = struct.unpack_from("<I", data, offset)[0]
            market.decoded_offsets["title_len"] = offset
            market.confidence["title_len"] = 0.4
            offset += 4

            if title_len > 0 and title_len < 1000 and len(data) >= offset + title_len:
                try:
                    market.title = data[offset:offset + title_len].decode("utf-8")
                except UnicodeDecodeError:
                    market.title = data[offset:offset + title_len].decode("latin-1")
                market.decoded_offsets["title"] = offset
                market.confidence["title"] = 0.5
                offset += title_len

        if len(data) >= offset + 4:
            url_len = struct.unpack_from("<I", data, offset)[0]
            if 0 < url_len < 2000 and len(data) >= offset + 4 + url_len:
                offset += 4
                try:
                    market.cover_image_url = data[offset:offset + url_len].decode("utf-8")
                except UnicodeDecodeError:
                    pass
                market.decoded_offsets["cover_url"] = offset
                market.confidence["cover_url"] = 0.3
                offset += url_len

        if len(data) >= offset + 4:
            link_len = struct.unpack_from("<I", data, offset)[0]
            if 0 < link_len < 2000 and len(data) >= offset + 4 + link_len:
                offset += 4
                try:
                    market.market_link = data[offset:offset + link_len].decode("utf-8")
                except UnicodeDecodeError:
                    pass
                market.decoded_offsets["link"] = offset
                market.confidence["link"] = 0.3

    def _decode_pool(self, market: DecodedMarket, data: bytes):
        pass

    def _decode_time(self, market: DecodedMarket, data: bytes):
        pass

    def _validate_strict(self, market: DecodedMarket):
        issues = []
        for field_name, conf in market.confidence.items():
            if conf < self.confidence_threshold:
                issues.append(
                    f"Field '{field_name}' confidence {conf:.2f} "
                    f"< threshold {self.confidence_threshold:.2f}"
                )
        if issues:
            market.decode_warnings.extend(issues)

    def decode_from_rpc(self, account_pubkey: str, rpc_url: str = "https://api.mainnet-beta.solana.com") -> DecodedMarket:
        resp = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [account_pubkey, {"encoding": "base64"}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["result"]
        if result is None:
            raise ValueError(f"Account {account_pubkey} not found")

        data_b64 = result["value"]["data"][0]
        raw_data = base64.b64decode(data_b64)
        return self.decode(account_pubkey, raw_data)

    def register_offsets(self, offsets: dict[str, int], confidences: dict[str, float]):
        for name, offset in offsets.items():
            for spec in self._layout:
                if spec.name == name:
                    spec.offset = offset
                    break
            else:
                self._layout.append(FieldSpec(
                    name=name, offset=offset, size=0, format="raw",
                    description=f"Discovered field: {name}",
                    confidence=confidences.get(name, 0.0),
                ))

        for name, conf in confidences.items():
            for spec in self._layout:
                if spec.name == name:
                    spec.confidence = conf
                    break

    def get_layout(self) -> list[FieldSpec]:
        return list(self._layout)

    def format_spec(self) -> str:
        lines = [
            "=== V2 Market Account Layout (DISCLAIMER: UNVERIFIED) ===",
            f"Program ID: {self.B4_PROGRAM_ID}",
            "",
            f"{'Field':<25} {'Offset':>8} {'Size':>6} {'Conf':>6} {'Category':<12} {'Description'}",
            "-" * 90,
        ]
        for spec in self._layout:
            conf_str = f"{spec.confidence:.0%}" if spec.confidence > 0 else "??"
            lines.append(
                f"  {spec.name:<23} {spec.offset:>6} 0x{spec.offset:04X}  "
                f"{spec.size:>4}  {conf_str:>6}  {spec.category:<12} {spec.description}"
            )
        lines.append("")
        lines.append("CONFIDENCE SCALE: ??? = unknown, 0-50% = placeholder, 50-95% = partial, 95%+ = verified")
        lines.append("WARNING: This layout is auto-generated and UNVERIFIED.")
        lines.append("Use v2_decoder.discover() to refine offsets from live data.")
        return "\n".join(lines)
