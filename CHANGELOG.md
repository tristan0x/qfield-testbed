# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `qft` command line with the deployment lifecycle (`add`, `list`, `show`, `start`, `stop`, `restart`,
  `remove`, `logs`, `launch`, `run`, `env`), `doctor`, `spec example|validate`, `cache list|prune` and
  `config show|path`.
- Deployment specs with version sources `release`, `git`, `worktree`, `artifact`, `build`, `host` and `docker`.
- Slot allocation: each deployment owns a block of 100 host ports and a Docker network.
- Glossary and documentation skeleton.
