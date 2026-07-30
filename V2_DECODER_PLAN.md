# V2 Market Account Decoder — Implementation Plan

**Date**: July 24, 2026
**Status**: Planning
**Goal**: Reverse-engineer the B4 Solana V2 market account layout and build a standalone decoder library

---

## 1. Context

### What We Know
- **Program**: `9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH`
- **Network**: Solana mainnet-beta
- **Market type**: Non-custodial USDC parimutuel polls
- **V2 mechanics**: Time-weighted stakes (2x first hour → 0x final 10 min)
- **Existing bot**: Python (pyTelegramBotAPI), lives in `b4-pool-bot/`
- **Public API**: Only returns V1 markets; V2 markets are on-chain but not in the API
- **No Python locally** on this machine

### What We Need
- Raw on-chain account bytes for V2 markets
- Account layout schema (field offsets, types, sizes)
- Validation against the B4 app display
- A standalone Python decoder module

### Key Constraints
- The program is NOT open-source (no GitHub repo found)
- No on-chain IDL confirmed yet (need to fetch via RPC)
- Public Solana RPC may rate-limit `getProgramAccounts` calls
- Need a paid RPC (Helius/QuickNode) for reliable bulk queries

---

## 2. Approach: Three-Phase Discovery

### Phase 1: IDL Discovery (Fastest Path)
**Objective**: Check if the program has an on-chain IDL that reveals the account layout directly.

**Steps**:
1. Use `@solana/foundation/idl` or Anchor's `fetchIdl` to try fetching the IDL from:
   - Anchor IDL account PDA (legacy storage)
   - Program Metadata Program (PMP) (Anchor 1.0+)
2. If IDL exists → parse account types and field layout immediately
3. If no IDL → proceed to Phase 2

**Tools needed**:
- Node.js + `@coral-xyz/anchor` or `@solana-foundation/idl`
- Solana RPC endpoint (mainnet-beta)

**Expected outcome**: Either we get the IDL (best case) or confirm it doesn't exist (proceed to manual discovery).

### Phase 2: Manual Account Layout Discovery
**Objective**: Reverse-engineer the account layout by comparing raw bytes with known API values.

**Steps**:
1. Fetch the public API response for several V1 markets (we have `market_pubkey` for each)
2. Use `getAccountInfo` RPC to fetch raw bytes for those same accounts
3. Cross-reference known fields (title, end_time, yes_pool, no_pool) with byte positions
4. Build a partial offset map

**Known fields to anchor against**:
| Field | Type | Notes |
|-------|------|-------|
| `market_id` | string/u64 | The numeric market ID |
| `title` | string (borsh) | Variable length, prefixed with u32 |
| `end_time` | i64/u64 | Unix timestamp |
| `yes_pool` | u64 | USDC lamports (6 decimals) |
| `no_pool` | u64 | USDC lamports |
| `yes_votes` | u32/u64 | Vote count |
| `no_votes` | u32/u64 | Vote count |
| `creator` | Pubkey | 32 bytes |
| `mechanics_version` | u8/u32 | 1 = V1, 2 = V2 |

**Strategy**:
- Search for the `market_pubkey` bytes in the account data (it may be a PDA seed)
- Search for the title string (UTF-8 bytes) to find its offset
- Search for known `end_time` values (unix timestamps like ~1784589343)
- Search for known pool values as little-endian u64

### Phase 3: V2 Market Discovery & Diff Analysis
**Objective**: Discover V2-specific fields by comparing accounts over time.

**Steps**:
1. Use `getProgramAccounts` with `dataSize` filter to find all accounts owned by the program
2. Filter for accounts that are NOT in the public API (these are V2 markets)
3. Save raw snapshots with timestamps
4. When votes occur, diff the byte arrays to identify vote-related offsets
5. Compare decoded values with B4 app display

**Snapshot archive structure**:
```
snapshots/
  {market_pubkey}/
    {timestamp}.bin     # Raw account bytes
    {timestamp}.json    # Known app values at that time
```

---

## 3. Decoder Module Design

### File Structure
```
b4-pool-bot/
  v2_decoder/
    __init__.py          # Public API
    decoder.py           # Core decoder logic
    account_layout.py    # Layout definitions (versioned)
    rpc_client.py        # Solana RPC wrapper
    snapshotter.py       # Snapshot archive manager
    validator.py         # Validation against app values
    spec.md              # Account specification document
```

### Core Classes

```python
# v2_decoder/account_layout.py

class AccountLayout:
    """Versioned account layout definitions."""
    
    VERSION_1 = {
        "discriminator": bytes(8),  # Anchor discriminator
        "fields": [
            {"name": "market_id", "type": "u64", "offset": 8, "size": 8},
            # ... populated by discovery
        ]
    }
    
    VERSION_2 = {
        # V2-specific fields
    }
```

```python
# v2_decoder/decoder.py

class V2MarketDecoder:
    """Decode B4 V2 market account data."""
    
    def __init__(self, layout_version="auto", strict_mode=True, min_confidence=0.95):
        self.layout = self._load_layout(layout_version)
        self.strict_mode = strict_mode
        self.min_confidence = min_confidence
    
    def decode_market(self, raw_bytes: bytes) -> "MarketData":
        """Decode full market account."""
        ...
    
    def decode_votes(self, raw_bytes: bytes) -> "VoteData":
        """Decode vote counts and pools."""
        ...
    
    def decode_weights(self, raw_bytes: bytes) -> "WeightData":
        """Decode time-weighted values."""
        ...
    
    def decode_status(self, raw_bytes: bytes) -> "StatusData":
        """Decode market status and flags."""
        ...
    
    def decode_metadata(self, raw_bytes: bytes) -> "Metadata":
        """Decode title, timestamps, creator, etc."""
        ...
```

```python
# v2_decoder/rpc_client.py

class SolanaRPCClient:
    """Wrapper for Solana JSON-RPC."""
    
    def __init__(self, rpc_url: str, api_key: str = None):
        ...
    
    def get_account(self, pubkey: str) -> bytes:
        """Fetch raw account data."""
        ...
    
    def get_program_accounts(self, program_id: str, 
                             data_size: int = None,
                             filters: list = None) -> list:
        """Fetch all accounts owned by the program."""
        ...
    
    def get_account_info(self, pubkey: str) -> dict:
        """Fetch account info with metadata."""
        ...
```

```python
# v2_decoder/snapshotter.py

class SnapshotArchive:
    """Binary snapshot archive for market accounts."""
    
    def __init__(self, archive_dir: str = "snapshots"):
        ...
    
    def save_snapshot(self, pubkey: str, raw_bytes: bytes, 
                      app_values: dict = None) -> str:
        """Save raw bytes with timestamp."""
        ...
    
    def load_snapshots(self, pubkey: str) -> list:
        """Load all snapshots for a market."""
        ...
    
    def diff_snapshots(self, pubkey: str, ts1: str, ts2: str) -> dict:
        """Find byte-level differences between two snapshots."""
        ...
    
    def find_changed_offsets(self, pubkey: str) -> list:
        """Identify offsets that changed across snapshots."""
        ...
```

```python
# v2_decoder/validator.py

class DecoderValidator:
    """Validate decoded values against known sources."""
    
    def __init__(self, decoder: V2MarketDecoder, rpc_client: SolanaRPCClient):
        ...
    
    def validate_field(self, field_name: str, 
                       decoded_value, expected_value) -> "ValidationResult":
        """Compare a single decoded field against expected value."""
        ...
    
    def validate_market(self, pubkey: str, 
                        api_data: dict = None,
                        app_screenshot: dict = None) -> "ValidationReport":
        """Full validation of a decoded market."""
        ...
    
    def generate_report(self, validations: list) -> "ConfidenceReport":
        """Generate confidence report across all validations."""
        ...
```

---

## 4. Offset Documentation Format

```markdown
## V2 Market Account Layout

### Account Header (bytes 0-7)
| Offset | Size | Type | Field | Confidence | Notes |
|--------|------|------|-------|------------|-------|
| 0 | 8 | bytes | discriminator | 100% | Anchor account discriminator |

### Market Core (bytes 8-...)
| Offset | Size | Type | Field | Confidence | Notes |
|--------|------|------|-------|------------|-------|
| 8 | 8 | u64 | market_id | 100% | Matches API market_id |
| 16 | 4 | u32 | title_length | 100% | Borsh string prefix |
| 20 | var | string | title | 100% | UTF-8, matches API title |
| ... | ... | ... | ... | ... | ... |
```

---

## 5. Validation Report Format

```markdown
## Confidence Report — Market B2G8Qv6Ftq9Lcc9joeJTsx4Wy7KoqUD15eYVPTRG1rsT

| Field | Offset | Expected | Decoded | Match | Confidence |
|-------|--------|----------|---------|-------|------------|
| market_id | 8-15 | 1784502669600491 | 1784502669600491 | ✅ | 100% |
| title | 20-89 | "Should political..." | "Should political..." | ✅ | 100% |
| end_time | 90-97 | 1784589343 | 1784589343 | ✅ | 100% |
| yes_pool | 98-105 | 20790000 | 20790000 | ✅ | 100% |
| no_pool | 106-113 | 4950000 | 4950000 | ✅ | 100% |
| yes_votes | 114-117 | 10 | ? | ❓ | 0% — NOT YET VALIDATED |
| mechanics_version | 118 | 1 | ? | ❓ | 0% — NOT YET VALIDATED |
```

---

## 6. Implementation Steps

### Step 1: Environment Setup
- Install Node.js (for IDL fetching tools)
- Set up Python virtual environment with `solana` and `base58` packages
- Configure RPC endpoint (Helius recommended for reliability)

### Step 2: IDL Discovery Attempt
- Use `@solana-foundation/idl` to fetch IDL for `9XQDD38sy1qJ57DqAQvADuLRTjcYUXD48H7deyNuaehH`
- If successful → parse layout, skip to Step 5
- If not → proceed to Step 3

### Step 3: V1 Account Layout Discovery
- Fetch 3-5 known V1 markets from public API
- Fetch raw account bytes via `getAccountInfo`
- Cross-reference known fields to build partial layout
- Document offsets with confidence scores

### Step 4: V2 Account Discovery
- Use `getProgramAccounts` to find all program-owned accounts
- Identify accounts NOT in the public API (likely V2)
- Save snapshots of V2 accounts

### Step 5: Decoder Implementation
- Build `V2MarketDecoder` with discovered layout
- Implement `decode_market()`, `decode_votes()`, `decode_weights()`, `decode_status()`
- Add strict mode (return "unknown" below confidence threshold)

### Step 6: Snapshot Archive
- Build `SnapshotArchive` for binary snapshot management
- Save initial snapshots of all discovered V2 markets
- Set up periodic snapshot collection

### Step 7: Validation
- Run decoder against known V1 markets (compare with API)
- Compare V2 decoded values against B4 app display
- Generate confidence report
- Document unknown fields

### Step 8: Documentation
- Write `spec.md` with full account layout
- Document all discovered offsets with confidence
- List fields still unknown

---

## 7. Dependencies

### Python Packages
```
solana>=0.34.0
base58>=2.1.0
construct>=2.10.0    # For structured binary parsing
requests>=2.31.0
```

### Node.js Packages (for IDL fetching)
```
@coral-xyz/anchor
@solana/web3.js
```

### RPC Provider
- **Recommended**: Helius (free tier available, supports `getProgramAccounts`)
- **Alternative**: QuickNode, Alchemy, or custom validator
- **Public RPC**: May work for `getAccountInfo` but unreliable for bulk queries

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| No on-chain IDL | High | Fall back to manual discovery (Phase 2) |
| Program uses non-Anchor layout | Medium | Use `construct` library for flexible binary parsing |
| Account layout changes between markets | Medium | Version the layout, detect by account size |
| Public RPC rate limits | Medium | Use paid RPC (Helius) |
| V2 fields are encrypted/obfuscated | High | Check if fields are visible in Solana Explorer |
| Title encoding varies | Low | Try UTF-8 and UTF-16, check both |

---

## 9. Success Criteria

- [ ] Account layout discovered with ≥90% confidence for core fields
- [ ] Decoder module successfully decodes V1 markets (matches API)
- [ ] Decoder module successfully decodes V2 markets (matches app)
- [ ] Snapshot archive saves and loads raw bytes correctly
- [ ] Validation report generated with confidence scores
- [ ] Unknown fields documented
- [ ] No hardcoded offsets without evidence

---

## 10. Next Steps After This Plan

Once the decoder is validated:
1. Integrate into the Notify Bot (import shared decoder)
2. Integrate into the Live Tracker
3. Add real-time V2 market monitoring
4. Build V2-specific notification templates
