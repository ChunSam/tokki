# Testing the Windows Port in a VM on the Mac

The development Mac has no `dotnet`, so CI used to be the only place the C# code
was compiled, and nothing exercised the Windows app against a real Claude Code.
A Windows 11 Arm VM under UTM closes both gaps: it runs the Windows tests,
publishes the app the way the release does, and runs Claude Code natively. Set
up on 2026-11-03 on an M1 Pro (32 GB).

## Setting up the VM

1. `brew install --cask utm crystalfetch`.
2. Fetch a Windows 11 **arm64** ISO with CrystalFetch, or from Microsoft's
   [Windows 11 for Arm download](https://www.microsoft.com/software-download/windows11arm64).
   An x64 ISO does not boot on Apple silicon.
3. In UTM: **+ → Virtualize → Windows**, 4 cores, 8 GB, 64 GB disk, with
   **Install drivers and SPICE tools** checked. Leave 3D acceleration off: UTM's
   accelerated display needs a guest driver Windows does not have.
4. Boot and press a key at *Press any key to boot from CD or DVD*. The prompt
   lasts a few seconds; missing it drops to the UEFI shell, where
   `FS0:`, `cd EFI\BOOT`, `BOOTAA64.EFI` starts the installer again.
5. When setup insists on a Microsoft account, Shift+F10 and
   `start ms-cxh:localonly` creates a local one.
6. In the guest, run the UTM guest tools installer from the tools CD (or the
   toolbar's CD menu, *Install Windows Guest Tools…*) and restart. That installs
   the QEMU guest agent, which is what lets the Mac drive the VM.

`timeout 30 utmctl ip-address Windows` answering is the sign the agent is up.

## Driving it from the Mac

Wrap direct `utmctl` calls in a host-side timeout, e.g. `timeout 30 utmctl status
Windows`. A bare `utmctl status` sat past two minutes on 2026-11-27, and exit 124
or an empty result leaves the VM state unknown. The timeout argument of
`scripts/utm-windows-ps.sh` limits only its completion-marker polling, so put an
outer timeout around the whole call when it must be bounded.

`scripts/utm-windows-ps.sh` runs a PowerShell script read from stdin inside the
VM and prints its output; `--user` runs it as the signed-in user. Feed scripts
with a quoted heredoc (`<<'EOF'`): zsh's `echo` turns `\U` in `C:\Users` into an
escape.

```bash
scripts/utm-windows-ps.sh --user 120 <<'EOF'
whoami; $env:PROCESSOR_ARCHITECTURE
EOF
```

What the script works around, each of which cost a debugging round:

- `utmctl exec` returns when the process starts, not when it ends. The script's
  end is a marker file that is polled for — by content, because `utmctl file
  pull` of a missing file still exits 0.
- The agent runs as `NT AUTHORITY\SYSTEM` and is an x64 process, so its children
  see `PROCESSOR_ARCHITECTURE=AMD64` on an Arm64 guest. Installers run that way
  land in the wrong profile and may pick the x64 build. `--user` goes through a
  one-shot `schtasks /it` task in the user's session instead.
- Windows PowerShell 5.1 writes UTF-16LE with a byte-order mark; `cmd` output is
  in the system code page (CP949 on a Korean install). It also reads a script
  without a byte-order mark in that code page, garbling anything outside ASCII,
  so the script is pushed with a UTF-8 BOM.
- Windows PowerShell's `ConvertFrom-Json` fails on `~/.claude.json` and prints
  the entire file in its error. Search that file with `Select-String`.

Source goes in as an archive pushed through the agent:

```bash
git archive --format=zip HEAD windows scripts | timeout 60 utmctl file push Windows 'C:\Users\Public\tt-src.zip'
```

## Provisioning

Run as the user (`--user`):

- Claude Code: `irm https://claude.ai/install.ps1 | iex`. It installs the native
  Arm64 build (PE machine `0xAA64`) to `%USERPROFILE%\.local\bin\claude.exe`,
  the first place the app looks. The installer does not add that folder to PATH.
- .NET 10 SDK, per user and without admin rights: Microsoft's
  `dotnet-install.ps1 -Channel 10.0 -InstallDir $env:LOCALAPPDATA\Microsoft\dotnet`.
- Then, from the expanded source:
  `dotnet run --project windows/TokenTracker.Windows.Tests/TokenTracker.Windows.Tests.csproj`,
  and the release's publish command with `-r win-arm64`.

Signing in to Claude Code is the one step that stays manual: run `claude` in a
terminal inside the VM and finish the browser sign-in there. That sign-in is the
VM's own and does not touch the Mac's credentials.

## Driving the app's windows

Checking the resizable settings window (2026-11-05, PR #83) took six rounds;
these are what cost them.

- Publish with the release's flags (`--self-contained true
  /p:PublishSingleFile=true`). A framework-dependent build only shows "You must
  install .NET Desktop Runtime": the per-user SDK is not a registered runtime.
- Click menu items with `mouse_event` on the UIA element's bounding rectangle,
  not with `InvokePattern`. Invoking the tray menu's `설정...` opened the form
  with its controls never painted, and a later `SetWindowPos` on it never
  returned.
- Call `SetProcessDPIAware()` first so UIA rectangles, `GetWindowRect` and the
  cursor are all in physical pixels.
- Resize another process's window with `SWP_ASYNCWINDOWPOS` (`0x4000`); a
  synchronous `SetWindowPos` waits on that window's thread. A user's drag is a
  left-down 3 px above `GetWindowRect`'s bottom (inside Windows 11's invisible
  resize border), absolute `mouse_event` moves, and a left-up.
- PowerShell passes `-150` in a command argument as a string, and `$dy * $i`
  then repeats it. Type the parameters (`[int]$dy`).
- `Stop-Process -Force` leaves the tray icon behind until the pointer passes
  over it, and right-clicking that ghost opens nothing. Sweep the pointer across
  the tray before looking for the new icon.
- A script that dies between left-down and left-up leaves the button held; start
  the next one with a left-up.
- The script's output reaches the Mac only when it ends, so a hung step shows
  nothing at all.

## Renewal test

[Claude token renewal](../README.md#claude-토큰-자동-갱신) can be tested without
waiting out the 8-hour token. On Windows the credential store is a plain file, so
with the app stopped, a copy of `%USERPROFILE%\.claude\.credentials.json` taken,
and `expiresAt` rewritten to an hour ago (as UTF-8 without a BOM), the next app
start finds the token expired.

Measured on 2026-11-03 with Claude Code 2.1.317, sampling every 0.5 s:

| | |
| --- | --- |
| App starts `claude.exe` (its parent is `TokenTracker.Windows.exe`) | +0.6 s |
| Credentials file rewritten, `expiresAt` 8 h ahead | +1.3 s |
| App's `claude.exe` last seen | +1.8 s |
| Claude usage | read from the API again |

A second run against main at `1dc3e4d` repeated it for both published builds:
win-arm64 renewed at +1.3 s, and win-x64, running under the guest's x64
emulation, at +1.6 s. Neither left a `claude.exe` behind.

No console window appeared, no other `claude.exe` was left behind, and the
refresh folder got no project entry or session. Unlike the macOS
pseudo-terminal launch, the hidden-console launch rewrote `~/.claude.json` once.
Delete the credential copy afterwards: the refresh token in it has been rotated
and no longer works.
