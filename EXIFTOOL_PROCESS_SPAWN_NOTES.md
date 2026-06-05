# ExifTool Process Spawning Analysis

## How an exiftool process is born

```
ExifToolSession()  (connect='auto' is the default)
  └─ ExifToolClient() → _ensure_server() → spawn_server()
       └─ subprocess.Popen(exiftool_server.py)     ← 1 server subprocess
            └─ ExifToolSession(connect=None) → ExifToolHelper()
                 └─ subprocess.Popen(exiftool binary)  ← 1 real exiftool process
```

The **server subprocess** (`exiftool_server.py`) is a long-lived daemon.
The **exiftool binary** is managed by PyExifTool's `ExifToolHelper()` and stays running for the server's lifetime.

Two notable variants:

1. **Direct mode**: `ExifToolSession(connect=None)` → `ExifToolHelper()` directly (skips the server). Used only by `test_multiple_instances.py`.
2. **Mocked**: `ExifToolSession(helper=mock_helper)` or `MagicMock(spec=ExifToolSession)` — zero processes.

---

## Per-test-file breakdown

### Test files that spawn exiftool processes

| Test file | Server subprocesses | Real exiftool binaries | Mechanism |
|---|---|---|---|
| **test_exiftool_server.py** | **Up to 4** (1 in setUpClass, 1 explicit in test_shutdown, 1 in test_idle_timeout, 1 auto-spawned by TestClientAutoSpawn) | **Up to 3** (each server spawns one exiftool binary, except the idle-timeout test's server) | `subprocess.Popen(exiftool_server.py)` + `ExifToolClient()` auto-spawn |
| **test_multiple_instances.py** | **0** | **Up to 5 at once, 10 total** across 2 test methods | `ExifToolSession(connect=None)` → direct `ExifToolHelper()` |
| **test_pid_election.py** | **Up to 22** (2 in test_lower_pid_wins, 20 in test_ten_servers_concurrent_lowest_pid_wins) | **0** (all use `--no-exiftool` flag) | `subprocess.Popen(exiftool_server.py)` |
| **test_full_auto_integration.py** | **1** (auto-spawned) | **1** | `ExifToolSession()` → auto-spawn |

### Test files that do NOT spawn exiftool processes

| Test file | Strategy |
|---|---|
| test_gps.py | Mocked `ExifToolHelper` |
| test_calibration_panel.py | `MagicMock(spec=ExifToolSession)` |
| test_history.py | `MagicMock(spec=ExifToolSession)` |
| test_analysis.py | `MagicMock()` |
| test_unit.py | Only imports `_parse_dt`; `env_check` probes binary path (no spawn) |
| test_btime.py | No exiftool usage |
| test_btime_gui_correction.py | No exiftool usage |
| test_parallel_loop_race.py | No exiftool usage |
| test_strategy.py | No exiftool usage |
| test_img.py | No exiftool usage |
| test_fuse_faketime.py | No exiftool usage |
| test_ubuntu_compat.py | No exiftool usage |
| test_gui_structure.py | No exiftool usage |
| test_file_table.py | No exiftool usage |
| test_datepicker.py | No exiftool usage |
| test_proposals.py | No exiftool usage |
| test_common_prefix.py | No exiftool usage |
| test_autocomplete.py | No exiftool usage |
| test_dst_fold.py | No exiftool usage |
| test_editor.py | No exiftool usage |
| test_preview.py | No exiftool usage |
| test_kernel_cache_coherence/test_cache_coherence.py | No exiftool usage |
| hypothesis/test_h9_to_h12.py | No exiftool usage |

---

## CI scope summary

| CI scope | Tests included | Max exiftool binaries spawned |
|---|---|---|---|
| `unit` | test.test_unit | 0 (env_check probes binary, no spawn) |
| `full` | test_btime_gui_correction, test_full_auto_integration | 1 (from test_full_auto_integration) |

Tests that DO spawn exiftool (`test_exiftool_server.py`, `test_multiple_instances.py`, `test_pid_election.py`) are **not in the CI matrix** — they are run manually via `nix run .#test`.

---

## Concurrency notes

| Test file | Max concurrent exiftool processes | Details |
|---|---|---|
| **test_exiftool_server.py** | 1 (or 2 briefly during election/test_shutdown) | Shared server design; only one exiftool binary per server |
| **test_multiple_instances.py** | **5** | `test_independent_sessions`: 5 direct-mode sessions sequentially created. `test_concurrent_available`: 5 threads each with their own direct-mode session |
| **test_pid_election.py** | **0** (--no-exiftool) | Only server wrappers, no actual exiftool binary |
| **test_full_auto_integration.py** | 1 | Via shared server |
