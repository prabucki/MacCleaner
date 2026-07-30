# Migration from the legacy scripts

Every capability of the two scripts MacCleaner replaces, and where it went. Anything
deliberately *not* carried over says so and why.

Source material, preserved verbatim:

- [MasterCleanScript.sh](MasterCleanScript.sh) — 329 lines of `sudo rm -rf`
- `~/Drive/Macbook/mac-scripts` — `cleanup.sh` and friends (`prabucki/mac-scripts`)

`tests/test_mc_modules.py::test_replaced_script_capabilities_are_covered` asserts that
each capability below still maps to a registered module, so this document cannot silently
drift from the code.

---

## MasterCleanScript.sh

### Privilege and setup

| Original | Now |
|---|---|
| `sudo -v` + keepalive loop | `mc-root` with a NOPASSWD sudoers rule. No prompt, no background loop holding a sudo session open on an unattended machine. |
| `sudo mount -uw /` | **Dropped.** Only needed because SIP is disabled on this Mac; nothing that survived the migration writes to the sealed system volume. |

### Updating

| Original | Now |
|---|---|
| `mac-cleanup -f` | The whole tool. Upstream's engine is the foundation; its modules are triaged in `mc/modules/upstream.py`. |
| `brew cleanup -s`, `rm -rf $(brew --cache)` | `homebrew` module — `brew cleanup -s --prune=all` plus the cache directory. |
| `npm update -g` | Update phase (topgrade's `node` step). |
| oh-my-zsh `upgrade.sh` | Update phase — invoked directly, because topgrade's `shell` step needs `$ZSH` exported, which launchd does not provide. |
| `brew upgrade --cask --greedy` | Update phase, via `greedy_cask = true` in `config/topgrade.toml`. |

### Caches

| Original | Now |
|---|---|
| `sudo rm -rf ~/Library/Caches/*` (appears **three** times) | `user_caches`, once. |
| `sudo rm -rf /Library/Caches/*` | `system_caches`, via `mc-root`. |
| `sudo rm -rf ~/.cache/*` | `user_caches`. |
| `sudo rm -rf ~/Library/Application Support/coreMLCache/*` | `user_caches`. 3.5 GB here. |
| `sudo rm -rf ~/Library/Application Support/Caches/*` | `user_caches`. |
| `rm -rf ~/Library/Containers/$x/Data/Library/Caches/*` loop | `container_caches`, as a glob. |
| `sudo rm -rf /private/var/folders/1v/x2bn002s3cz0c5jc0g7sy9bh0000gn/C/*` | `system_caches` — **the hardcoded UUID is gone.** That path is derived from the user's identity and changes; the glob `/private/var/folders/*/*/C/*` finds it wherever it is. |
| `atsutil databases -removeUser`, `server -shutdown`, `-ping` | `font_cache`, plus the root-side `atsutil databases -remove` the original could not do. |
| `sudo rm -f /private/var/db/BootCache.playlist` | `system_caches`. |
| `sudo rm -fr /var/db/coreduet/*` | `system_caches`. |

### Logs

| Original | Now |
|---|---|
| `sudo rm -rf ~/Library/logs/*`, `/Library/logs/*`, `/var/log/*`, `/private/var/log/*` | `user_logs` and `system_logs`. |
| — | **`unified_log` is new and matters.** Since macOS 10.12 most logging lives in `/private/var/db/diagnostics`, managed by `logd`, which recreates whatever you delete underneath it. The original script's `/private/var/log/*` never reclaimed it. `log erase --all` does, and it is routinely gigabytes. |

### System maintenance

| Original | Now |
|---|---|
| `sudo purge` | `memory_purge` |
| `sudo periodic daily weekly monthly` | `periodic_scripts` |
| `sudo dscacheutil -flushcache && killall -HUP mDNSResponder` | `dns_cache` |
| `lsregister -kill -r -seed -domain local -domain system -domain user` | `launch_services` |
| `sudo touch /System/Library/Extensions && sudo kextcache -u /` | `kext_cache`, moved to the **nuclear** tier. It touches the boot path, and on an Apple Silicon Mac with no third-party kexts it is a no-op at best. The original ran it on every invocation. |
| `sudo update_dyld_shared_cache` (commented out) | Left out — deprecated, as the comment said. |
| `sudo /usr/libexec/xpchelper --rebuild-cache` (commented out) | Left out. |

### Developer tooling

| Original | Now |
|---|---|
| `xcrun simctl delete unavailable` | `xcode_simulators` |
| Xcode Archives, DerivedData, `CoreSimulator/Caches/dyld` | `xcode`, `xcode_archives` |
| `rm -rf /private/var/folders/dk/*/C/com.apple.DeveloperTools/*/` | `system_caches` (covered by the generic per-boot cache glob) |
| AppleTVOS/WatchOS `DeviceSupport` and simulator profiles | `xcode_device_support`, generalised to iOS/watchOS/tvOS/macOS |
| Removing whole Xcode platforms (commented out — "breaks XCode") | Left out. The comment was right. |
| `clean_keep_latest` for `ndk/` and `platforms/` | `android_sdk`, extended to `build-tools` and `system-images`. See below for why it was rewritten. |
| `sudo rm -rf ~/.gradle/*` | `android_caches` — **narrowed.** The original deleted all of `~/.gradle`, including `gradle.properties` (your signing config and JVM args). Now only caches, daemon state and wrapper archives go. |
| `npm cache clean --force`, `rm -rf ~/.npm/_npx` | `node` |
| `sudo rm -rf ~/.nvm/.cache/*` | `nvm`, which also prunes superseded Node versions |
| `gem cleanup` | `ruby` |
| `nuget locals all -clear` | Available as `upstream_nuget_cache`; .NET is not installed here so it self-skips. |

**Why `clean_keep_latest` was rewritten rather than ported.** The original did
`cd "$TARGET_DIR" || exit 1` — a missing directory aborted the *entire* script, silently
skipping every later cleanup step. It also parsed `ls -1d` output, which breaks on names
containing spaces, and then fed that parse straight into `rm -rf`. The replacement
enumerates with `pathlib`, sorts with a real version comparison, and routes through the
same policy-checked deletion as everything else.

### Applications

| Original | Now |
|---|---|
| `pkill -x Teams` then delete `~/Library/Application Support/Microsoft/Teams/` | `microsoft_teams` — quits gracefully first, and covers the newer container-based Teams too. Note the original deleted the *whole* directory, not just caches. |
| Four hardcoded Ferdi service-UUID cache paths | `communication_apps` and `electron_apps`, globbing `Partitions/service-*`. **Those UUIDs were already stale** — the app is Ferdium now. |
| `~/Library/Application Support/Cleanshot/Media/*` | `setapp_apps` |
| `~/Library/Application Support/Transmit/Logs/*` | `misc_apps` |
| BetterTouchTool clipboard history | `misc_apps` |
| Duplicate File Finder logs | `misc_apps` |
| `/Users/Shared/Blizzard/Battle.net/Cache` | `misc_apps` (privileged) |
| `/Users/Shared/Adobe/Premiere Pro/1*/*` | `adobe_shared` |
| MS Office cask installers, by name | `homebrew` — `brew cleanup --prune=all` handles every cask generically. |
| `find "$(brew --prefix)/Caskroom" -name '*.pkg' -delete` | `homebrew`. **Deliberately not a like-for-like port:** that blanket delete also removed the installer for the *currently installed* version, which Homebrew needs to uninstall a cask cleanly. `brew cleanup --prune=all` removes only superseded ones. |
| Illustrator sample-script folders, hardcoded to `Adobe Illustrator 2025` | `adobe_shared`, globbed to `/Applications/Adobe Illustrator */` so it survives upgrades. |
| `plutil -remove FirstRunDate ~/Library/Preferences/com.apple.finder.plist` | **Dropped.** An XtraFinder trial reset; XtraFinder is not installed. |

### Deliberately not carried over

| Original | Why |
|---|---|
| `rm -rf /System/Library/Speech/Voices/Daniel.SpeechVoice` etc. | Available in the `nuclear` tier only. Removing system voices breaks Accessibility features and they return on OS updates. |
| The `.lproj` language-stripping `find` | The original had already commented it out as *"This seems to be breaking my Macbook"*. It also invalidates code signatures on every bundle it touches. `nuclear` tier, off by default. |
| The entire Adobe bloatware block | Commented out in the original pending a licensing change. Not resurrected; `adobe_caches` and `adobe_shared` cover the safe parts. |
| Adobe LaunchAgents/LaunchDaemons removal | `/Library/LaunchDaemons` and `/Library/LaunchAgents` are hard-protected. Removing an updater's launch agent breaks it in ways that surface weeks later. `broken_login_items` reports stale ones instead. |
| System wallpaper removal | Already disabled in the original ("lack of system access since Big Sur"). |

---

## mac-scripts

| Original | Now |
|---|---|
| `cleanup.sh` (runs every script in `scripts/`) | `mc` itself. |
| `cleanup_trash.sh` — `rm -rf ~/.Trash/*` | `trash`, which also covers `/Volumes/*/.Trashes`. |
| `cleanup_cache.sh` — `rm -rf ~/Library/Caches/*` | `user_caches`. |
| `cleanup_cache.sh` — `sudo rm -rf /tmp/*` | `temp_files`, **with an age filter.** The original was a real hazard: `/tmp` holds active unix sockets, PID files and lock files for running processes, and deleting them causes failures that are near-impossible to trace back to a cleanup script. Only entries untouched for 3+ days are removed. |
| `cleanup_DS_Store.sh` — `find "$HOME" -name .DS_Store -delete` | `ds_store`, **which does not touch Downloads.** The original swept the entire home directory. This one prunes Downloads (hard-protected), credential stores, and large irrelevant trees, so it does not walk 74 GB of Application Support to find a few kilobytes. |
| `update_nvm.sh` | Update phase. Same flow: install latest LTS, `nvm reinstall-packages` from the old version, uninstall it, `nvm cache clear`, repoint `default`, ensure corepack. |
| `clean_react_native.sh` — global caches (`~/.metro`, `~/.rncache`, `~/.flipper`, watchman) | `react_native` module. |
| `clean_react_native.sh` — per-project artefacts | `mc --project-clean <dir>`, generalised past React Native to Cargo, Maven, Gradle, Flutter, SwiftPM and Python projects. |

### Differences from `clean_react_native.sh`

- **`xcrun simctl erase all` is not run.** The original called it while cleaning a single
  project. It wipes *every* simulator on the machine — all installed apps, all login
  state, for every project you work on. `xcode_simulators` uses
  `simctl delete unavailable`, which only removes simulators whose runtime is already
  gone.
- **`safe_remove "$HOME/.gradle/daemon"`** is in `android_caches` rather than the project
  cleaner; it is machine-scoped, not project-scoped.
- **It refuses to run outside a project root.** The original would happily operate on
  whatever directory you happened to be in.
- **Project type is detected**, so a Rust or Python checkout gets the right treatment
  instead of React Native assumptions.

---

## Retiring the originals

Both are safe to delete once you have run `mc --dry-run` and are happy with the coverage.

`MasterCleanScript.sh` is preserved in this repository at
[docs/legacy/MasterCleanScript.sh](MasterCleanScript.sh).

`mac-scripts` is its own git repository (`git@github.com:prabucki/mac-scripts.git`), so
its history survives independently — archive the GitHub repo rather than deleting it, and
remove the local checkout at `~/Drive/Macbook/mac-scripts`.
