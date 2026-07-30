"""Validation framework comparing decoded V2 market values vs B4 API / app."""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from .decoder import V2MarketDecoder, DecodedMarket, MarketStatus


@dataclass
class ValidationEntry:
    field_name: str
    decoded_value: Any
    api_value: Any
    match: bool
    confidence: float
    note: str = ""


@dataclass
class ValidationReport:
    account_pubkey: str
    timestamp: float
    entries: list[ValidationEntry] = field(default_factory=list)
    overall_confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def matches(self) -> int:
        return sum(1 for e in self.entries if e.match)

    @property
    def mismatches(self) -> int:
        return sum(1 for e in self.entries if not e.match)

    @property
    def total(self) -> int:
        return len(self.entries)

    def format(self) -> str:
        lines = [
            f"=== Validation Report: {self.account_pubkey[:16]}... ===",
            f"Timestamp: {self.timestamp:.0f}",
            f"Overall confidence: {self.overall_confidence:.1%}",
            f"Matches: {self.matches}/{self.total}",
            f"Mismatches: {self.mismatches}/{self.total}",
            "",
        ]
        for e in self.entries:
            status = "OK" if e.match else "MISMATCH"
            lines.append(
                f"  [{status:>8}] {e.field_name:<25} "
                f"decoded={str(e.decoded_value):<30} "
                f"api={str(e.api_value):<30} "
                f"conf={e.confidence:.0%}"
            )
            if e.note:
                lines.append(f"           {e.note}")

        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


class Validator:
    B4_API_BASE = "https://www.b4app.xyz/api/markets"

    def __init__(self, decoder: Optional[V2MarketDecoder] = None):
        self.decoder = decoder or V2MarketDecoder()
        self._session = requests.Session()

    def fetch_api_market(self, market_pubkey: str) -> Optional[dict]:
        try:
            resp = self._session.get(
                f"{self.B4_API_BASE}/{market_pubkey}",
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def validate(
        self,
        account_pubkey: str,
        decoded: DecodedMarket,
        api_data: Optional[dict] = None,
    ) -> ValidationReport:
        report = ValidationReport(
            account_pubkey=account_pubkey,
            timestamp=time.time(),
        )

        if api_data is None:
            api_data = self.fetch_api_market(account_pubkey)

        if api_data is None:
            report.warnings.append(
                "No API data available for comparison. "
                "Decoded values cannot be cross-validated."
            )
            for field_name, conf in decoded.confidence.items():
                report.entries.append(ValidationEntry(
                    field_name=field_name,
                    decoded_value=getattr(decoded, field_name, "N/A"),
                    api_value=None,
                    match=False,
                    confidence=conf,
                    note="No API reference available",
                ))
            report.overall_confidence = 0.0
            return report

        self._validate_field(report, "title", decoded.title, api_data.get("title"))
        self._validate_status(report, decoded.status, api_data.get("status"))
        self._validate_field(report, "market_link", decoded.market_link, api_data.get("market_link"))
        self._validate_field(report, "cover_image_url", decoded.cover_image_url, api_data.get("cover_image_url"))
        self._validate_field(report, "market_id", decoded.market_id, api_data.get("market_id"))

        if api_data.get("market_pubkey"):
            report.entries.append(ValidationEntry(
                field_name="account_pubkey",
                decoded_value=decoded.account_pubkey,
                api_value=api_data["market_pubkey"],
                match=decoded.account_pubkey == api_data["market_pubkey"],
                confidence=1.0,
            ))

        confidences = [e.confidence for e in report.entries if e.confidence > 0]
        report.overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        if report.mismatches > 0:
            report.warnings.append(
                f"{report.mismatches} fields did not match API values. "
                "Offset calibration may be needed."
            )

        return report

    def _validate_field(
        self,
        report: ValidationReport,
        field_name: str,
        decoded_value: Any,
        api_value: Any,
    ):
        if api_value is None:
            report.entries.append(ValidationEntry(
                field_name=field_name,
                decoded_value=decoded_value,
                api_value=None,
                match=False,
                confidence=decoded.confidence.get(field_name, 0.0),
                note="Field not in API response",
            ))
            return

        match = str(decoded_value).strip() == str(api_value).strip()
        report.entries.append(ValidationEntry(
            field_name=field_name,
            decoded_value=decoded_value,
            api_value=api_value,
            match=match,
            confidence=decoded.confidence.get(field_name, 0.0),
        ))

    def _validate_status(
        self,
        report: ValidationReport,
        decoded_status: MarketStatus,
        api_status: Optional[str],
    ):
        status_map = {
            "active": MarketStatus.ACTIVE,
            "resolved_yes": MarketStatus.RESOLVED_YES,
            "resolved_no": MarketStatus.RESOLVED_NO,
            "cancelled": MarketStatus.CANCELLED,
        }
        expected = status_map.get(api_status, MarketStatus.UNKNOWN)
        match = decoded_status == expected
        report.entries.append(ValidationEntry(
            field_name="status",
            decoded_value=decoded_status.name,
            api_value=api_status,
            match=match,
            confidence=decoded.confidence.get("status_raw", 0.0),
        ))

    def validate_against_app(
        self,
        decoded: DecodedMarket,
        app_yes_count: Optional[int] = None,
        app_no_count: Optional[int] = None,
        app_pool_yes: Optional[float] = None,
        app_pool_no: Optional[float] = None,
    ) -> ValidationReport:
        report = ValidationReport(
            account_pubkey=decoded.account_pubkey,
            timestamp=time.time(),
        )

        if app_yes_count is not None:
            report.entries.append(ValidationEntry(
                field_name="total_yes_votes",
                decoded_value=decoded.total_yes_votes,
                api_value=app_yes_count,
                match=abs(decoded.total_yes_votes - app_yes_count) < 1,
                confidence=decoded.confidence.get("total_yes_votes", 0.0),
            ))

        if app_no_count is not None:
            report.entries.append(ValidationEntry(
                field_name="total_no_votes",
                decoded_value=decoded.total_no_votes,
                api_value=app_no_count,
                match=abs(decoded.total_no_votes - app_no_count) < 1,
                confidence=decoded.confidence.get("total_no_votes", 0.0),
            ))

        if app_pool_yes is not None:
            report.entries.append(ValidationEntry(
                field_name="yes_pool",
                decoded_value=decoded.pool.yes_pool,
                api_value=app_pool_yes,
                match=abs(decoded.pool.yes_pool - app_pool_yes) < 0.01,
                confidence=decoded.confidence.get("yes_pool", 0.0),
            ))

        if app_pool_no is not None:
            report.entries.append(ValidationEntry(
                field_name="no_pool",
                decoded_value=decoded.pool.no_pool,
                api_value=app_pool_no,
                match=abs(decoded.pool.no_pool - app_pool_no) < 0.01,
                confidence=decoded.confidence.get("no_pool", 0.0),
            ))

        confidences = [e.confidence for e in report.entries if e.confidence > 0]
        report.overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return report
