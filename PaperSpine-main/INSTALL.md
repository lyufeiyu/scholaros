# PaperSpine5 V5 installation

PaperSpine5 publishes separate self-contained suites for Windows x64, Linux
x86_64 with glibc, Apple Silicon Macs, and Intel Macs. Do not interchange
platform packages. Every suite includes its matching Python runtime, the Web
workspace, the canonical `paper-spine` Skill, and transactional profile
update/rollback.

## Which archive do I need?

Only the **platform suites** (26–56 MB) contain the V5 Web workspace and the
embedded runtime. The **standalone Skill** archive (0.72 MB) is the lightweight
option for a host that already has a V5 runtime and product core of its own; it
ships the Skill files but no Web core.

Installing the standalone archive on a host without that runtime produces this
as soon as the Skill tries to open the Web workspace:

```text
The standalone PaperSpine5 Web core is missing. Rebuild/install the current
self-contained paper-spine Skill; do not fall back to the terminal intake UI.
```

That message means the wrong archive was installed for this host — it is not a
broken download and not a missing release asset. Download the suite for your
platform instead.

Without the Web workspace the Skill can still do local work on an
already-configured task, but configuration, user choices, previews and
downloads all go through Web, so a fresh task cannot be started that way.

## Windows x64

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex -CleanLegacy
```

Use `-Target claude-code` or `-Target both` as appropriate.

## macOS and Linux

```sh
sh ./install.sh --target codex --clean-legacy
```

Use `--target claude-code` or `--target both` as appropriate. The installer
auto-detects the OS and CPU, downloads only the matching suite, verifies its
exact byte count and SHA-256, verifies the suite internally, installs the Skill,
activates the profile, and requires first-start REST/MCP readiness.

To check without changing the installation:

```sh
sh ./install.sh --check-only
```

To verify a previously downloaded package without downloading it again:

```sh
sh ./install.sh --bundle /path/to/platform-suite.zip --target codex --clean-legacy
```

On Windows, **both** paths are required. The POSIX installer carries the
platform file name, byte count and SHA-256 inside itself, but the PowerShell
installer reads them from the manifest, so `-BundlePath` alone still needs the
network:

```powershell
.\install.ps1 -ManifestPath .\manifest.json -BundlePath .\paperspine5-suite-0.4.0-alpha.1-dev.zip -Target codex -CleanLegacy
```

Both installers archive the existing canonical `paper-spine` Skill. Cleanup of
known V3/V4 discovery names happens only when `-CleanLegacy` or
`--clean-legacy` is supplied. They do not delete paper task data, host settings,
or unknown folders. Restart the host after installation.

The default profile is `%USERPROFILE%\.paperspine5\profiles\default` on
Windows and `~/.paperspine5/profiles/default` on macOS/Linux. Automatic update
is disabled by default; rerunning the platform installer performs an explicit
transactional update and retains task data.

macOS packages in this prerelease are unsigned and not notarized. Linux support
is limited to glibc x86_64; Linux arm64 and musl/Alpine are not claimed.

## When the download itself fails

Some corporate networks, TLS-inspecting proxies and antivirus products cannot
reach `github.com` at all. The handshake then fails before any HTTP status
exists, so the error looks like a missing file:

```text
SEC_E_NO_CREDENTIALS
Could not create SSL/TLS secure channel
The underlying connection was closed: An error occurred on receive
```

These are local network/TLS conditions. The release assets are published and
downloadable from a healthy connection — confirm that from another machine
before concluding that a release is missing.

In order of effort:

1. Check whether something is intercepting TLS:

   ```powershell
   netsh winhttp show proxy
   ```

2. Fetch the manifest from the website mirror. It is a different host and is
   sometimes reachable when `github.com` is not:

   <https://wubing2023.github.io/PaperSpine/v5/downloads/manifest.json>

   It is a copy of the same manifest. The suite ZIPs are only published as
   GitHub Release assets, so the mirror alone cannot complete an install.

3. Install fully offline. Download `manifest.json` and the suite ZIP for this
   platform on a machine that can reach GitHub, copy both over, then run the
   command for your platform from the section above. The installer still checks
   the byte count and SHA-256 against the manifest, so a copied file is verified
   exactly like a downloaded one.
