# PaperSpine5 rollback

Current prerelease: `v0.4.0-alpha.1-dev`. The installer stores prior canonical Skill bytes and profile installations under `.paperspine5`; task data stays separate. Use the suite `paperspine.cmd rollback` command with an installed build ID, or restore an intentional timestamped Skill backup after closing the host.

Never overwrite a published ZIP without updating its manifest and SHA-256. If a release is defective, withdraw it and publish a new version. Git history retains older tags; old build reports are not kept in current `main`.
