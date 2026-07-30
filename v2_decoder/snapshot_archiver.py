"""Fetch and archive raw Solana account bytes for V2 market accounts."""

import json
import os
import struct
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests


B4_PROGRAM_ID = "9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH"
DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
SNAPSHOT_DIR = Path(__file__).parent.parent / "v2_snapshots"


@dataclass
class AccountSnapshot:
    account_pubkey: str
    lamports: int
    owner: str
    data_hex: str
    data_bytes: bytes
    data_length: int
    executable: bool
    rent_epoch: int
    timestamp: float
    snapshot_index: int


@dataclass
class SnapshotMeta:
    account_pubkey: str
    index: int
    timestamp: float
    data_length: int
    filename: str


class SnapshotArchiver:
    def __init__(
        self,
        rpc_url: str = DEFAULT_RPC,
        snapshot_dir: Optional[Path] = None,
    ):
        self.rpc_url = rpc_url
        self.snapshot_dir = snapshot_dir or SNAPSHOT_DIR
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()

    def _rpc_call(self, method: str, params: list) -> dict:
        resp = self._session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"RPC error: {body['error']}")
        return body["result"]

    def fetch_account(self, pubkey: str) -> AccountSnapshot:
        result = self._rpc_call("getAccountInfo", [
            pubkey,
            {"encoding": "base64"},
        ])
        if result is None:
            raise ValueError(f"Account {pubkey} not found")

        info = result["value"]
        if info is None:
            raise ValueError(f"Account {pubkey} has no data")

        import base64
        data_b64 = info["data"][0]
        data_bytes = base64.b64decode(data_b64)
        data_hex = data_bytes.hex()

        return AccountSnapshot(
            account_pubkey=pubkey,
            lamports=info["lamports"],
            owner=info["owner"],
            data_hex=data_hex,
            data_bytes=data_bytes,
            data_length=len(data_bytes),
            executable=info["executable"],
            rent_epoch=info["rentEpoch"],
            timestamp=time.time(),
            snapshot_index=0,
        )

    def get_existing_snapshots(self, account_pubkey: str) -> list[SnapshotMeta]:
        acct_dir = self.snapshot_dir / account_pubkey
        meta_file = acct_dir / "meta.json"
        if not meta_file.exists():
            return []
        with open(meta_file) as f:
            data = json.load(f)
        return [SnapshotMeta(**entry) for entry in data]

    def save_snapshot(self, snapshot: AccountSnapshot) -> Path:
        acct_dir = self.snapshot_dir / snapshot.account_pubkey
        acct_dir.mkdir(parents=True, exist_ok=True)

        existing = self.get_existing_snapshots(snapshot.account_pubkey)
        index = len(existing)
        snapshot.snapshot_index = index

        filename = f"snapshot_{index}_{int(snapshot.timestamp)}.bin"
        bin_path = acct_dir / filename
        with open(bin_path, "wb") as f:
            f.write(snapshot.data_bytes)

        hex_path = acct_dir / f"snapshot_{index}_{int(snapshot.timestamp)}.hex"
        with open(hex_path, "w") as f:
            f.write(snapshot.data_hex)

        meta_entry = SnapshotMeta(
            account_pubkey=snapshot.account_pubkey,
            index=index,
            timestamp=snapshot.timestamp,
            data_length=snapshot.data_length,
            filename=filename,
        )
        existing.append(meta_entry)
        meta_file = acct_dir / "meta.json"
        with open(meta_file, "w") as f:
            json.dump([asdict(e) for e in existing], f, indent=2)

        print(f"[snapshot] Saved #{index} for {snapshot.account_pubkey[:12]}... "
              f"({snapshot.data_length} bytes, {len(existing)} total)")
        return bin_path

    def load_snapshot(self, account_pubkey: str, index: int) -> bytes:
        acct_dir = self.snapshot_dir / account_pubkey
        existing = self.get_existing_snapshots(account_pubkey)
        if index >= len(existing):
            raise IndexError(f"Snapshot #{index} not found (have {len(existing)})")
        meta = existing[index]
        bin_path = acct_dir / meta.filename
        with open(bin_path, "rb") as f:
            return f.read()

    def fetch_and_snapshot(self, pubkey: str) -> AccountSnapshot:
        snapshot = self.fetch_account(pubkey)
        self.save_snapshot(snapshot)
        return snapshot

    def batch_fetch_and_snapshot(self, pubkeys: list[str]) -> list[AccountSnapshot]:
        snapshots = []
        for pk in pubkeys:
            try:
                snap = self.fetch_and_snapshot(pk)
                snapshots.append(snap)
            except Exception as e:
                print(f"[snapshot] Error fetching {pk[:12]}...: {e}")
            time.sleep(0.5)
        return snapshots

    def discover_program_accounts(
        self, program_id: str = B4_PROGRAM_ID, min_size: int = 0, max_size: int = 0
    ) -> list[dict]:
        result = self._rpc_call("getProgramAccounts", [
            program_id,
            {"encoding": "base64", "withContext": True},
        ])
        accounts = result.get("value", result) if isinstance(result, dict) else result
        filtered = []
        for acct in accounts:
            import base64
            data = base64.b64decode(acct["account"]["data"][0])
            if min_size and len(data) < min_size:
                continue
            if max_size and len(data) > max_size:
                continue
            filtered.append({
                "pubkey": acct["pubkey"],
                "size": len(data),
                "lamports": acct["account"]["lamports"],
                "owner": acct["account"]["owner"],
            })
        filtered.sort(key=lambda x: x["size"], reverse=True)
        print(f"[discover] Found {len(filtered)} accounts "
              f"(filtered from program {program_id[:12]}...)")
        return filtered

    def discover_v2_markets(self) -> list[dict]:
        accounts = self.discover_program_accounts(B4_PROGRAM_ID)
        print(f"[discover] All program accounts by size:")
        for a in accounts[:20]:
            print(f"  {a['pubkey'][:16]}...  size={a['size']}  lamports={a['lamports']}")
        return accounts
