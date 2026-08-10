# Security Policy

## Supported versions

Until the project reaches a stable 1.0 release, security fixes are applied to the latest development/release line only.

## Reporting a vulnerability

Please do **not** publish exploitable security issues, malicious test files, or sensitive paths/tokens in a public GitHub Issue.

If GitHub's **Private vulnerability reporting** option is enabled for this repository, use the repository's **Security → Report a vulnerability** flow. Otherwise, contact the repository owner privately through an appropriate GitHub contact route and provide only enough public information to establish contact.

Useful reports include:

- affected application version,
- operating system,
- exact reproduction steps,
- whether a crafted map/JAR/ZIP is required,
- expected and observed behaviour,
- impact assessment,
- a minimal safe proof of concept where possible.

The project processes untrusted archive and Minecraft/mod data, so archive traversal, unsafe extraction, path overwrite, decompression abuse, malformed NBT handling, and unsafe external-process invocation should be treated as security-sensitive areas.
