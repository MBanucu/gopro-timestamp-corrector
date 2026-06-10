# GoPro Timestamp Corrector

Correct the creation timestamps of GoPro videos and thumbnails when the camera's clock was wrong.

This is the **meta-repository** — it contains the project as three independent sub-projects:

- **[gopro-timestamp-sdk](./sdk)** — core SDK library: analysis, preview, planning, I/O, strategies
- **[gopro-timestamp-cli](./cli)** — CLI tool for batch-processing entire SD cards
- **[gopro-timestamp-gui](./gui)** — GUI application with live preview, calibration, and step-by-step workflow

## Quick start

```bash
# Clone with submodules
git clone --recursive https://github.com/MBanucu/gopro-timestamp-corrector.git
cd gopro-timestamp-corrector

# Install SDK, then CLI or GUI
pip install ./sdk
pip install ./cli    # for the CLI
pip install ./gui    # for the GUI

# Run
correct-gopro-timestamps --help    # CLI
gopro-timestamp-gui                # GUI
```

Or use Nix:

```bash
cd sdk && nix develop
# Then run CLI or GUI from their respective directories
```

## Project structure

```
├── sdk/       — gopro-timestamp-sdk   (core library)
├── cli/       — gopro-timestamp-cli   (command-line tool)
├── gui/       — gopro-timestamp-gui   (GUI application)
├── LICENSE
└── README.md
```

Each sub-project has its own `pyproject.toml`, CI, tests, and can be used independently.

## Requirements

- Python 3.10+
- `exiftool` (from your package manager)
- `sudo` with passwordless access (for birth time correction)
- Optional: `e2fsprogs`, `exfat`, `libfaketime` (for btime strategies)

## Documentation

See the documentation in each sub-project:

- [SDK documentation](./sdk/README.md)
- [CLI documentation](./cli/README.md)
- [GUI documentation](./gui/README.md)
