# Security Policy

The runtime is local-only and has no HTTP, socket, telemetry, cloud, or external-service integration. It accepts local JSON and writes a local SQLite database.

Input is untrusted. Strict topology validation is the default, imports are transactional, URLs/URIs/UNC shares/symlinks are rejected, and only textual message fields are normalized. Attachment metadata is not imported.

## Supported versions

Only the latest released version is supported with security updates. Before the first release, the source candidate is unreleased and is not covered by a released-version support commitment. Older releases become unsupported when a newer release is published unless this policy explicitly says otherwise.

## Reporting a vulnerability

Do not open a public GitHub Issue or post sensitive details in a public discussion.

GitHub Private Vulnerability Reporting is the intended private reporting mechanism. Use GitHub's **Report a vulnerability** flow for `rigret0311/gpt-log-oss` only when that option is visible:

https://github.com/rigret0311/gpt-log-oss/security/advisories/new

Do not attach a real private ChatGPT export, database, credential, personal path, or other sensitive user data. Use the smallest synthetic reproduction possible.

This policy does not claim that Private Vulnerability Reporting is currently enabled. Until the **Report a vulnerability** option is confirmed active, do not disclose vulnerability details in an Issue or public discussion. Enabling and verifying the setting is a release Human Gate. No public security email is provided.
