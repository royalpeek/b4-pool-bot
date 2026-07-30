# APK Investigation Plan

**Purpose**: When the B4 Android APK is obtained, this document guides the reverse engineering effort to extract API endpoints, authentication flows, and V2 data sources.

---

## 1. Prerequisites

### Tools Needed
| Tool | Purpose | Install |
|------|---------|---------|
| `apktool` | Decompile APK to smali/resources | `brew install apktool` or download JAR |
| `jadx` | Decompile to readable Java/Kotlin | `brew install jadx` or download JAR |
| `dex2jar` | Convert DEX to JAR for analysis | Download from GitHub |
| `JD-GUI` | Java decompiler GUI | Download from GitHub |
| `mitmproxy` | Intercept live HTTP/HTTPS traffic | `brew install mitmproxy` |
| `Android Studio` | Emulator for running APK | Download from Google |

### Alternative: Online Tools
- `https://www.decompiler.com/` — upload APK, get Java source
- `https://m.apktool.com/` — online APK decompiler

---

## 2. APK Acquisition

### Option A: Google Play (Recommended)
```
1. Install Aurora Store (F-Droid) or use APKPure
2. Search for "b4app" or "B4 Opinion Markets"
3. Download APK directly
```

### Option B: APK Mirror
```
1. Visit https://www.apkmirror.com/
2. Search "B4" or "b4app"
3. Download latest version
```

### Option C: Device Extraction (if you have the app installed)
```
1. Enable USB debugging on Android device
2. Connect via ADB
3. Run: adb shell pm path com.b4app.b4
4. Run: adb pull /path/to/base.apk
```

---

## 3. Static Analysis Steps

### Step 1: Decompile with jadx
```bash
jadx -d output/ b4.apk
```
This produces readable Java/Kotlin source in `output/sources/`

### Step 2: Search for API Endpoints

Priority search patterns:
```
# Retrofit interface definitions
grep -r "@GET\|@POST\|@PUT\|@DELETE\|@PATCH" output/sources/

# Base URL configuration
grep -r "baseUrl\|BASE_URL\|apiUrl\|API_URL" output/sources/

# WebSocket endpoints
grep -r "wss://\|ws://\|socket\|Socket\|websocket" output/sources/

# GraphQL
grep -r "graphql\|GraphQL\|query\s*{" output/sources/

# gRPC / Protobuf
grep -r "\.proto\|protobuf\|grpc\|Proto" output/sources/
```

### Step 3: Find Authentication Flow

```
# JWT / Token handling
grep -r "token\|jwt\|auth\|session\|cookie\|Bearer" output/sources/

# Wallet signing
grep -r "signMessage\|signTransaction\|wallet\|solana\|phantom" output/sources/

# Session management
grep -r "SharedPreferences\|Keychain\|secure存储" output/sources/
```

### Step 4: Find V2-Specific Code

```
# Mechanics version handling
grep -r "mechanics_version\|mechanicsVersion\|v2\|time.weight\|weighted" output/sources/

# Reputation system
grep -r "reputation\|gold\|platinum\|diamond\|tier" output/sources/

# Display weights
grep -r "display_weight\|displayWeight\|timeWeight" output/sources/
```

### Step 5: Find Network Configuration

```
# OkHttp interceptors (logging, auth injection)
grep -r "Interceptor\|addInterceptor\|addNetworkInterceptor" output/sources/

# Certificate pinning
grep -r "CertificatePinner\|ssl\|pinning" output/sources/

# Custom HTTP headers
grep -r "addHeader\|header(" output/sources/
```

---

## 4. Dynamic Analysis Steps

### Step 1: Setup MITM Proxy

```bash
# Start mitmproxy
mitmproxy --listen-port 8080

# Install mitmproxy CA on Android emulator/device
# Visit mitm.it to install certificate
```

### Step 2: Configure Android to Use Proxy

```
Settings → WiFi → Long press network → Modify Network → Advanced → Proxy → Manual
Proxy hostname: 10.0.2.2 (emulator) or computer IP
Proxy port: 8080
```

### Step 3: Capture Traffic

1. Open B4 app
2. Browse markets
3. Watch for:
   - REST API calls with auth headers
   - WebSocket connections
   - GraphQL queries
   - Protobuf payloads

### Step 4: Analyze Auth Flow

```
1. Clear app data
2. Start fresh session
3. Capture login/auth sequence
4. Note:
   - How wallet signature is used
   - What token/cookie is returned
   - Token refresh mechanism
   - Session expiry handling
```

---

## 5. Key Files to Examine

After decompilation, prioritize these paths:

```
sources/
├── com/b4app/                    # Main package
│   ├── api/                      # API client code
│   │   ├── *Service.kt          # Retrofit interfaces
│   │   ├── *Client.kt           # HTTP clients
│   │   └── *Interceptor.kt      # Auth/logging interceptors
│   ├── data/
│   │   ├── *Repository.kt       # Data sources
│   │   ├── *DataSource.kt       # Remote/local data
│   │   └── model/               # Data models
│   ├── di/                       # Dependency injection
│   │   └── NetworkModule.kt     # Network config
│   ├── feature/
│   │   ├── market/              # Market screens
│   │   ├── stake/               # Staking flow
│   │   └── profile/             # User profile/reputation
│   └── util/
│       ├── *Prefs.kt            # Shared preferences (tokens)
│       └── *Crypto.kt           # Wallet/crypto utils
```

---

## 6. What to Extract

### Priority 1: API Endpoints
- [ ] Base URL(s) for production
- [ ] All REST endpoint paths
- [ ] Request/response schemas
- [ ] Pagination patterns
- [ ] Query parameter options

### Priority 2: Authentication
- [ ] Auth token format (JWT? custom?)
- [ ] How wallet signature is used
- [ ] Token refresh mechanism
- [ ] Required headers for authenticated requests
- [ ] Session management

### Priority 3: V2 Data
- [ ] How V2 market data is fetched
- [ ] Time-weighted calculation endpoints
- [ ] Reputation tier endpoints
- [ ] Display weight endpoints
- [ ] Real-time updates (WebSocket?)

### Priority 4: Feature Flags
- [ ] PostHog keys used
- [ ] Feature flag evaluation
- [ ] A/B test configuration
- [ ] Remote config values

### Priority 5: Additional Channels
- [ ] WebSocket endpoints
- [ ] GraphQL schema (if any)
- [ ] Push notification registration
- [ ] Deep linking configuration

---

## 7. Expected Outcome

After APK analysis, we should have:

1. **Complete API documentation** — all endpoints, params, auth requirements
2. **V2 data access** — authenticated endpoint for V2 market data
3. **Real-time feeds** — WebSocket for live market updates
4. **Feature flag system** — ability to enable/disable features remotely
5. **Reputation data** — access to user tiers and history

---

## 8. Time Estimate

| Task | Time |
|------|------|
| APK acquisition | 10 min |
| jadx decompilation | 5 min |
| Static analysis (endpoints) | 1-2 hours |
| Static analysis (auth flow) | 1-2 hours |
| Dynamic analysis (traffic capture) | 1-2 hours |
| Documentation of findings | 1 hour |
| **Total** | **4-7 hours** |

---

## 9. Legal Considerations

- Reverse engineering for interoperability is generally permitted under DMCA §1201(f)
- Do not redistribute the APK or proprietary code
- Use findings only for building compatible integrations
- Respect B4's Terms of Service
