# GoPro Timestamp Corrector — meta-repo

## Submodules

- `sdk/` → github.com/MBanucu/gopro-timestamp-sdk
- `cli/` → github.com/MBanucu/gopro-timestamp-cli
- `gui/` → github.com/MBanucu/gopro-timestamp-gui

## Development workflow

```bash
# Clone with submodules
git clone --recursive ...

# Update all submodules
git submodule update --remote

# Install SDK + CLI in dev mode
pip install -e ./sdk
pip install -e ./cli

# Install SDK + GUI in dev mode
pip install -e ./sdk
pip install -e ./gui

# Run SDK tests
cd sdk && python -m unittest discover -s test -v

# Run CLI tests
cd cli && python -m unittest discover -s test -v

# Run GUI tests
cd gui && DISPLAY=:99 python -m unittest discover -s test -v
```
