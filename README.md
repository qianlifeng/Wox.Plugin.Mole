# Wox.Plugin.Mole

Native Wox commands for the official [Mole CLI](https://github.com/tw93/Mole).

Type `mo` to show the same five commands as Mole's main menu:

| Command | Description |
| --- | --- |
| Clean | Free up disk space |
| Uninstall | Remove apps completely |
| Optimize | Refresh caches and services |
| Analyze | Explore disk usage |
| Status | Monitor system health |

All five entries are declared in `plugin.json` as Wox `Commands`. When no command is active, the plugin returns all five commands as results. Search filters command names by case-insensitive substring (for example, `mo cl` or `mo op`). Enter navigates to the selected native Wox command. Clean opens its full-width preview without starting cleanup; Uninstall loads its application list.

In `mo clean`, Enter runs cleanup (`mo clean`). Ctrl+Enter runs a preview (`mo clean --dry-run`), or choose **Preview cleanup** in the Action Panel (Command+Enter on macOS). Entering the command page itself does not start either operation. The full preview displays the current state, phase and cleanup details grouped by Mole's own categories, with Mole-reported space figures. Windows reports Mole's tracked cleanup amount rather than an independently measured free-space change. Preview completion explicitly says that no files were removed. Nonzero exits and Mole-reported cleanup errors are shown as failures, including when Mole catches an error and exits zero. There is no estimated percentage when Mole supplies no total.

While running, the default action becomes **Stop**. **Back to commands** returns to the native menu without cancelling the session. Re-enter Clean to see its current state. Output is limited to 1,500 lines of 2,000 characters each, with a truncation notice. Unloading the plugin stops its process tree. A stopped cleanup cannot undo work Mole already completed.

The HTML dashboard keeps the status spinner and summary fixed. All observed categories and their item details appear in a separate scrollable area. New output follows the bottom until the user scrolls up; while paused, both new output and heartbeat updates preserve the reading position. Scrolling back to the bottom resumes following. Follow state stays in page memory, and a new cleanup starts following again. There is no horizontal progress bar or separate full-output action. Elapsed time sits below the top-right figure; preview mode has a small badge beside the status.

Each query returns complete HTML initialized with the latest task state. Subsequent `update_result` calls send `{"updateMode":"script","script":"window.updateCleanup(...)"}` with a JSON-serialized state snapshot. The page updates stable category/item nodes in place, preserving the spinner, scroll container and selected text. No session storage or `window.name` restoration is needed. Leaving the plugin query stops pushes without cancelling cleanup; re-entering Clean reconstructs the latest state without restarting it. `src/cleanup.js` is bundled alongside the Python source by the existing build rule.

**Uninstall on Windows** opens Mole's application list, sorted by size. Type after `mo uninstall` to filter application names. Select an application, then choose **Confirm uninstall** to pass that one application to Mole. Its own uninstaller may open a window. The list refreshes after completion; failures or an application still present in Mole's scan are reported rather than treated as success.

The adapter loads the installed `bin/uninstall.ps1` with `-ShowHelp` to avoid its terminal menu, then calls Mole's own `Get-InstalledApplications`, `Get-AppSelectionKey` and `Uninstall-SelectedApps`. These are internal Mole functions, so incompatible versions produce an error. No registry enumeration, installer command construction or leftover deletion is reimplemented in the plugin. Before uninstalling, Mole rescans and the adapter resolves the exact registry/package identity, rejecting ambiguous or missing entries. The adapter currently supports Windows only; macOS Uninstall reports that limitation.

Optimize, Analyze and Status use compact HTML previews and script updates. Enter starts Optimize and Analyze; while running, Enter stops them. These two commands retain their sessions when navigating away.

- Optimize streams `mo optimize` output into sections; Ctrl+Enter (Command+Enter on macOS) uses Mole's `--dry-run` mode. No actual optimization is run by tests.
- Status starts automatically on entering `mo status` and displays metric cards without a Monitoring header or elapsed timer. It uses Mole's `--watch --interval=2s` JSON stream. CPU, memory, disks, network and process snapshots replace previous values rather than growing a history log. Repeated queries reuse the running process; switching commands, returning to the command menu, or leaving the plugin query stops and reaps it. Re-entering starts a fresh monitor.
- Analyze uses `mo analyze --json <path>` (home directory by default; enter a path after `mo analyze`). Entries are size-sorted, with partial/unavailable measurements identified. Reports larger than 16 MB are rejected and displayed rows are bounded. Windows invokes Mole's own `bin/analyze.exe` because the PowerShell wrapper does not forward JSON options. The installed Windows binary must advertise `--json`; older TUI-only versions show an explicit unsupported-version message without launching the TUI. No separate scanner is implemented.

macOS command execution is supported through the official CLI but has not been tested on a Mac in this change.

The plugin does not independently scan or delete files. All cleanup is performed by Mole. Preview estimates are never reported as actual freed space.

## Requirements

- A local Wox build containing script-mode WebView updates, and Python 3.10+; SDK remains pinned to `wox-plugin==0.0.89`.
- This source change is not a release. Before publishing, set `MinWoxVersion` to the actual Wox release containing this capability; the existing manifest floor does not imply older hosts support script updates.
- macOS: `brew install mole`.
- Windows 10/11: `scoop install mole`, or follow the [official Windows CLI instructions](https://github.com/tw93/Mole/tree/windows).

The native Mole desktop app is a separate product. Restart Wox after changing PATH. Standard Homebrew, Windows script-install and Scoop locations are also detected. Custom installs must expose `mo.exe` or `mole.ps1` on Windows, or `mo` on macOS, on PATH. Legacy `mo.cmd` installs work when `mole.ps1` is beside the launcher.

Clean runs in a hidden background process on Windows and macOS. The plugin does not elevate permissions or accept interactive password prompts. These maintenance commands do not participate in MRU.

## Development

```text
uv sync --all-extras
uv run python -m unittest
uv run ruff check src tests
uv run mypy src
```

`tests/cleanup_dom.cjs` checks node identity, text preservation and scroll following.
For the opt-in Windows WebView2 test in the Wox repository, generate a simulated
fixture with `python -m tests.make_webview_fixture <temporary-json-path>` (or run the
script with this repository on `PYTHONPATH`). Set `WOX_WEBVIEW_SCRIPT_FIXTURE` to that
path and `WOX_WEBVIEW2_LOADER_PATH` to the local `WebView2Loader.dll`, then run
`go test ./ui/runtime/internal/window -run TestWindowsWebViewScriptFixture -count=1`
from `wox.core`. It checks real browser animation, selection and scrolling using
simulated output; it never executes Mole.
