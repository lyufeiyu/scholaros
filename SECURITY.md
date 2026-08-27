# Security

**English** | [简体中文](./SECURITY.zh-CN.md)

Do not paste API keys, `.env` files, databases, uploaded papers, or complete logs into public issues. If a secret is exposed, revoke and rotate it with the relevant provider before contacting the maintainers.

ScholarOS binds to the local machine by default. Before exposing it to a LAN or server, configure authentication, HTTPS, CSRF protection, and access logs at the reverse proxy or gateway. ScholarOS does not read browser cookies and does not provide paywall, CAPTCHA, robots restriction, or institutional-license bypasses.

Report security issues through a private channel and include reproduction steps, affected versions, and only the minimum necessary logs.
