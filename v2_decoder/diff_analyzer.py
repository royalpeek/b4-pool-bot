"""Byte-level diff analyzer for comparing V2 market account snapshots."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .snapshot_archiver import SnapshotArchiver


@dataclass
class ByteChange:
    offset: int
    old_value: int
    new_value: int
    old_bytes: bytes
    new_bytes: bytes
    context_before: bytes = b""
    context_after: bytes = b""


@dataclass
class DiffResult:
    account_pubkey: str
    index_a: int
    index_b: int
    timestamp_a: float
    timestamp_b: float
    size_a: int
    size_b: int
    changes: list[ByteChange] = field(default_factory=list)
    summary: str = ""

    @property
    def changed_offsets(self) -> list[int]:
        return sorted(set(c.offset for c in self.changes))

    @property
    def change_ranges(self) -> list[tuple[int, int]]:
        if not self.changes:
            return []
        offsets = self.changed_offsets
        ranges = []
        start = offsets[0]
        end = offsets[0]
        for o in offsets[1:]:
            if o <= end + 8:
                end = o
            else:
                ranges.append((start, end + 1))
                start = o
                end = o
        ranges.append((start, end + 1))
        return ranges


@dataclass
class DiffTimeline:
    account_pubkey: str
    diffs: list[DiffResult] = field(default_factory=list)
    cumulative_changes: dict[int, list] = field(default_factory=dict)


class DiffAnalyzer:
    def __init__(self, archiver: Optional[SnapshotArchiver] = None):
        self.archiver = archiver or SnapshotArchiver()

    def diff_snapshots(
        self,
        account_pubkey: str,
        index_a: int,
        index_b: int,
        context_size: int = 16,
    ) -> DiffResult:
        data_a = self.archiver.load_snapshot(account_pubkey, index_a)
        data_b = self.archiver.load_snapshot(account_pubkey, index_b)

        meta_a = self.archiver.get_existing_snapshots(account_pubkey)[index_a]
        meta_b = self.archiver.get_existing_snapshots(account_pubkey)[index_b]

        changes = []
        max_len = max(len(data_a), len(data_b))
        for offset in range(max_len):
            byte_a = data_a[offset] if offset < len(data_a) else None
            byte_b = data_b[offset] if offset < len(data_b) else None
            if byte_a != byte_b:
                old_val = byte_a if byte_a is not None else None
                new_val = byte_b if byte_b is not None else None
                ctx_before = data_a[max(0, offset - context_size):offset]
                ctx_after_a = data_a[offset + 1:offset + 1 + context_size]
                ctx_after_b = data_b[offset + 1:offset + 1 + context_size]
                changes.append(ByteChange(
                    offset=offset,
                    old_value=old_val if old_val is not None else -1,
                    new_value=new_val if new_val is not None else -1,
                    old_bytes=data_a[offset:offset + 1] if old_val is not None else b"",
                    new_bytes=data_b[offset:offset + 1] if new_val is not None else b"",
                    context_before=ctx_before,
                    context_after=ctx_after_b,
                ))

        time_delta = meta_b.timestamp - meta_a.timestamp
        size_change = len(data_b) - len(data_a)
        range_count = len(DiffResult(
            account_pubkey=account_pubkey, index_a=index_a, index_b=index_b,
            timestamp_a=meta_a.timestamp, timestamp_b=meta_b.timestamp,
            size_a=len(data_a), size_b=len(data_b), changes=changes
        ).change_ranges)

        result = DiffResult(
            account_pubkey=account_pubkey,
            index_a=index_a,
            index_b=index_b,
            timestamp_a=meta_a.timestamp,
            timestamp_b=meta_b.timestamp,
            size_a=len(data_a),
            size_b=len(data_b),
            changes=changes,
        )
        result.summary = (
            f"{len(changes)} byte changes across {range_count} ranges "
            f"over {time_delta:.1f}s, size {len(data_a)}->{len(data_b)} "
            f"({size_change:+d} bytes)"
        )
        return result

    def build_timeline(self, account_pubkey: str) -> DiffTimeline:
        snapshots = self.archiver.get_existing_snapshots(account_pubkey)
        if len(snapshots) < 2:
            return DiffTimeline(account_pubkey=account_pubkey)

        diffs = []
        cumulative: dict[int, list[ByteChange]] = {}
        for i in range(1, len(snapshots)):
            diff = self.diff_snapshots(account_pubkey, i - 1, i)
            diffs.append(diff)
            for c in diff.changes:
                if c.offset not in cumulative:
                    cumulative[c.offset] = []
                cumulative[c.offset].append(c)

        timeline = DiffTimeline(
            account_pubkey=account_pubkey,
            diffs=diffs,
            cumulative_changes=cumulative,
        )
        return timeline

    def find_stable_offsets(
        self, account_pubkey: str, min_diffs: int = 2
    ) -> list[tuple[int, list[ByteChange]]]:
        timeline = self.build_timeline(account_pubkey)
        stable = []
        for offset, changes in sorted(timeline.cumulative_changes.items()):
            if len(changes) >= min_diffs:
                stable.append((offset, changes))
        return stable

    def find_volatile_offsets(self, account_pubkey: str) -> list[tuple[int, list[ByteChange]]]:
        timeline = self.build_timeline(account_pubkey)
        volatile = []
        for offset, changes in sorted(timeline.cumulative_changes.items()):
            if len(changes) >= 1:
                unique_values = set(c.new_value for c in changes if c.new_value >= 0)
                if len(unique_values) > 2:
                    volatile.append((offset, changes))
        return volatile

    def format_diff_report(self, diff: DiffResult) -> str:
        lines = [
            f"=== Diff Report: {diff.account_pubkey[:16]}... ===",
            f"Snapshot A: #{diff.index_a} ({diff.timestamp_a:.0f})",
            f"Snapshot B: #{diff.index_b} ({diff.timestamp_b:.0f})",
            f"Size: {diff.size_a} -> {diff.size_b}",
            f"Changes: {len(diff.changes)} bytes",
            f"Ranges: {diff.change_ranges}",
            "",
        ]
        for c in diff.changes:
            hex_a = c.old_bytes.hex() if c.old_bytes else "N/A"
            hex_b = c.new_bytes.hex() if c.new_bytes else "N/A"
            ctx_hex = c.context_before.hex() if c.context_before else ""
            lines.append(
                f"  0x{c.offset:04X} ({c.offset:5d}): "
                f"0x{hex_a} -> 0x{hex_b}  "
                f"ctx_before=...{ctx_hex[-16:]}"
            )
        lines.append("")
        lines.append(f"Summary: {diff.summary}")
        return "\n".join(lines)

    def format_timeline_report(self, timeline: DiffTimeline) -> str:
        lines = [
            f"=== Timeline: {timeline.account_pubkey[:16]}... ===",
            f"Snapshots compared: {len(timeline.diffs)}",
            f"Unique offsets changed: {len(timeline.cumulative_changes)}",
            "",
        ]
        for offset, changes in sorted(timeline.cumulative_changes.items()):
            unique_vals = sorted(set(c.new_value for c in changes if c.new_value >= 0))
            lines.append(
                f"  0x{offset:04X} ({offset:5d}): "
                f"{len(changes)} changes, "
                f"unique values: {[f'0x{v:02X}' for v in unique_vals]}"
            )
        return "\n".join(lines)
