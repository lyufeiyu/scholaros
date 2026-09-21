# PaperSpine5 V5 update

Use this playbook only for an explicit update/check request. It does not start or
modify a paper task. V3/V4 `paperspine_update.py` and
`dist/paperspine_version.json` are legacy component routes, not the V5 release
channel.

## Check the current V5 channel

The stable public channel is:

```text
https://wubing2023.github.io/PaperSpine/v5/downloads/manifest.json
```

On Windows, download that JSON and its `release_assets.installer_url` into a new
temporary directory. Before running the installer, compare its byte count and
SHA-256 with `release_assets.installer_bytes` and
`release_assets.installer_sha256`. Stop on any mismatch. Then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -CheckOnly -ManifestPath .\manifest.json
```

The result is one of `not_installed`, `up_to_date`, or `update_available`, based
on the profile's actual active build ID rather than the older component version.
Checking does not replace files.

## Apply an explicitly requested update

After a verified `update_available` result, use the same verified installer and
manifest:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target codex -ManifestPath .\manifest.json
```

Use `-Target claude-code` or `-Target both` when requested. An existing V5 profile
uses the transactional lifecycle `update`; it verifies the suite, retains task
data, runs REST/MCP readiness and first-start, and requires a new host session.
Use `-CleanLegacy` only when the user explicitly wants known V3/V4 discovery
folders archived. Never delete unknown folders, settings, or paper data.

The self-contained full-suite update is currently Windows x64 only. For another
platform, update only the standalone Skill using its verified release archive and
package installer; do not claim full-suite runtime validation.

Compatibility note: the installed legacy component helper remains at
`paper-spine\scripts\paperspine_update.py` on Windows and
`paper-spine/scripts/paperspine_update.py` on POSIX. Do not use it as the V5
release authority; the verified manifest/installer route above supersedes it.
