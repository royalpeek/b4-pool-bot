"""
V2 Research Script - run this to discover and decode V2 market accounts.

Usage:
    python -m v2_decoder.research                  # discover all program accounts
    python -m v2_decoder.research snapshot <pk>    # fetch and snapshot a specific account
    python -m v2_decoder.research diff <pk>        # diff snapshots for an account
    python -m v2_decoder.research decode <pk>      # attempt to decode an account
    python -m v2_decoder.research layout           # print current layout spec
"""

import json
import sys
import time
from pathlib import Path

from .snapshot_archiver import SnapshotArchiver, B4_PROGRAM_ID
from .diff_analyzer import DiffAnalyzer
from .decoder import V2MarketDecoder
from .validation import Validator


def cmd_discover():
    print("=== Discovering B4 Program Accounts ===")
    print(f"Program: {B4_PROGRAM_ID}")
    print()

    archiver = SnapshotArchiver()
    accounts = archiver.discover_v2_markets()

    print()
    print(f"Total accounts: {len(accounts)}")
    print()

    if not accounts:
        print("No accounts found. The program may not have any on-chain accounts yet.")
        return

    print("Size distribution:")
    sizes = [a["size"] for a in accounts]
    print(f"  Min: {min(sizes)} bytes")
    print(f"  Max: {max(sizes)} bytes")
    print(f"  Avg: {sum(sizes) / len(sizes):.0f} bytes")
    print()

    print("Top 10 largest accounts:")
    for i, acct in enumerate(accounts[:10]):
        print(f"  {i+1}. {acct['pubkey']}")
        print(f"     Size: {acct['size']} bytes, Lamports: {acct['lamports']}")
    print()

    out_path = Path(__file__).parent.parent / "v2_snapshots" / "discovered_accounts.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(accounts, f, indent=2)
    print(f"Saved to {out_path}")


def cmd_snapshot(pubkey: str):
    print(f"=== Snapshotting Account: {pubkey[:16]}... ===")
    archiver = SnapshotArchiver()
    snap = archiver.fetch_and_snapshot(pubkey)
    print(f"Size: {snap.data_length} bytes")
    print(f"Owner: {snap.owner}")
    print(f"Lamports: {snap.lamports}")
    print(f"Hex preview: {snap.data_hex[:128]}...")


def cmd_diff(pubkey: str):
    print(f"=== Diffing Snapshots: {pubkey[:16]}... ===")
    archiver = SnapshotArchiver()
    analyzer = DiffAnalyzer(archiver)

    snapshots = archiver.get_existing_snapshots(pubkey)
    print(f"Available snapshots: {len(snapshots)}")

    if len(snapshots) < 2:
        print("Need at least 2 snapshots to diff. Run 'snapshot' command multiple times.")
        return

    timeline = analyzer.build_timeline(pubkey)
    print(analyzer.format_timeline_report(timeline))

    latest_diff = analyzer.diff_snapshots(pubkey, len(snapshots) - 2, len(snapshots) - 1)
    print(analyzer.format_diff_report(latest_diff))


def cmd_decode(pubkey: str):
    print(f"=== Decoding Account: {pubkey[:16]}... ===")
    decoder = V2MarketDecoder(strict=False)

    try:
        market = decoder.decode_from_rpc(pubkey)
    except Exception as e:
        print(f"RPC decode failed: {e}")
        print("Trying local snapshot...")
        archiver = SnapshotArchiver()
        try:
            snapshots = archiver.get_existing_snapshots(pubkey)
            if not snapshots:
                print("No snapshots found. Run 'snapshot' first.")
                return
            latest = archiver.load_snapshot(pubkey, len(snapshots) - 1)
            market = decoder.decode(pubkey, latest)
        except Exception as e2:
            print(f"Local decode also failed: {e2}")
            return

    print(f"Market ID: {market.market_id}")
    print(f"Title: {market.title}")
    print(f"Status: {market.status.name}")
    print(f"Creator: {market.creator_pubkey}")
    print(f"Link: {market.market_link}")
    print(f"Cover: {market.cover_image_url}")
    print(f"Pool YES: {market.pool.yes_pool}")
    print(f"Pool NO: {market.pool.no_pool}")
    print(f"Votes YES: {market.total_yes_votes}")
    print(f"Votes NO: {market.total_no_votes}")
    print(f"Participants: {market.participant_count}")
    print()
    print("Confidence scores:")
    for field_name, conf in sorted(market.confidence.items()):
        print(f"  {field_name}: {conf:.0%}")
    print()
    if market.decode_warnings:
        print("Warnings:")
        for w in market.decode_warnings:
            print(f"  - {w}")

    validator = Validator(decoder)
    report = validator.validate(pubkey, market)
    print(report.format())


def cmd_layout():
    decoder = V2MarketDecoder()
    print(decoder.format_spec())


def cmd_help():
    print(__doc__)


def main():
    if len(sys.argv) < 2:
        cmd_help()
        return

    command = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if command == "discover":
        cmd_discover()
    elif command == "snapshot":
        if not arg:
            print("Usage: python -m v2_decoder.research snapshot <account_pubkey>")
            return
        cmd_snapshot(arg)
    elif command == "diff":
        if not arg:
            print("Usage: python -m v2_decoder.research diff <account_pubkey>")
            return
        cmd_diff(arg)
    elif command == "decode":
        if not arg:
            print("Usage: python -m v2_decoder.research decode <account_pubkey>")
            return
        cmd_decode(arg)
    elif command == "layout":
        cmd_layout()
    else:
        cmd_help()


if __name__ == "__main__":
    main()
