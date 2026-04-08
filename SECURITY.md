# Security Policy

This project expects the web UI to be deployed behind a trusted reverse proxy when it is exposed beyond localhost. Mutating admin routes are rate limited, but the strongest deployment is still an internal-only web bind with proxy-enforced authentication.

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not open a public issue**.

Instead, report it privately via [GitHub Security Advisories](../../security/advisories/new).

Please include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes if you have them

You can expect an acknowledgement within 48 hours and a resolution or update within 7 days.

## Supported Versions

Security fixes are applied to the current `main` branch and the latest released version.
