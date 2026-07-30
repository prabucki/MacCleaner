# MacCleaner

Comprehensive, fully headless macOS cleanup, updating and maintenance.

A fork of [mac-cleanup-py](https://github.com/mac-cleanup/mac-cleanup-py) with the parts
needed to run unattended: a path safety policy, quarantine instead of deletion, root
escalation through a verb-limited helper, timeouts on everything, and ~70 cleanup modules
covering what CleanMyMac and OnyX do.

```
mc --breakdown    # readable summary of what would be deleted, folders rolled up
mc --dry-run      # totals only, touch nothing
mc                # clean at the default (aggressive) profile
mc --restore ...  # undo a run
```

`--breakdown` is the one to run before a first real cleanup. It groups by module, rolls
near-identical paths up to their common directory, and shows an item count instead of
sixty lines of per-service cache UUIDs:

```
electron_apps  10.93 GB
       9.56 GB    58x  ~/Library/Application Support/Ferdium/
     653.99 MB    18x  ~/Library/Application Support/Code/
     524.48 MB    13x  ~/Library/Application Support/Notion/
      19.82 MB    50x  + 13 more locations
```

Add `--breakdown-all` to list every path instead.

### Reviewing before it runs

A manual `mc` run stops and shows the same plan, letting you deselect anything before
a single file is touched:

```
Review before deleting — 25.85 GB selected
  #        Module                   Size  Locations
  1    on  electron_apps         9.63 GB         58
  2    on  communication_apps    9.50 GB         52
  3    on  user_caches           4.31 GB          2
  4  skip  unified_log                 —          1

numbers toggle a module · d <n> pick locations inside one · all / none · go to run · q to cancel
review>
```

`d 1` drills into a module to deselect individual locations. `q` or Ctrl-C cancels
without deleting anything.

The gate appears **only when stdin and stdout are both terminals**, so a scheduled run
never blocks waiting for input. Skip it with `--no-review` or `--yes`; force it in a
pipeline with `--review`.

---

## What it replaces

| Replaced | Where it went |
|---|---|
| `MasterCleanScript.sh` (329 lines of `sudo rm -rf`) | [docs/legacy/MIGRATION.md](docs/legacy/MIGRATION.md) maps every line |
| `mac-scripts/` (`cleanup.sh`, `cleanup_cache.sh`, `cleanup_DS_Store.sh`, `cleanup_trash.sh`, `update_nvm.sh`, `clean_react_native.sh`) | [docs/legacy/MIGRATION.md](docs/legacy/MIGRATION.md) |
| `mac-cleanup-py` on its own | Vendored as the engine; its modules are triaged in [mc/modules/upstream.py](mc/modules/upstream.py) |
| `topgrade` run by hand | The update phase, with a headless config in [config/topgrade.toml](config/topgrade.toml) |
| CleanMyMac | Orphaned-app leftovers, Electron sweeper, system junk, updater |
| OnyX | Periodic scripts, cache rebuilds, Launch Services, font caches, disk verification |

---

## Install

```bash
git clone git@github.com:prabucki/MacCleaner.git
cd MacCleaner
./install.sh              # asks for your password exactly once
./install.sh --schedule   # ...and installs the weekly LaunchAgent
```

Then grant **Full Disk Access** to `/usr/local/libexec/maccleaner/mc-run` in
System Settings → Privacy & Security. Without it, everything under `~/Library/Containers`,
Mail and Safari is invisible and those modules report zero rather than failing.

Check it worked:

```bash
mc --doctor
```

---

## How it runs without a password

Two mechanisms, both installed by `install.sh`:

**`mc-root`** — a root-owned helper at `/usr/local/libexec/maccleaner/mc-root`, granted
passwordless sudo by `/etc/sudoers.d/maccleaner`. This is narrower than it sounds:

- It accepts a **fixed vocabulary of verbs**. There is no "run this command" verb, so
  adding a capability means editing a root-owned file.
- It **re-validates every path itself**, as root, after expanding globs — it never trusts
  a path list from the unprivileged side.
- It **refuses to run** if it or its copy of the policy is not root-owned and
  non-user-writable.
- It runs under `/usr/bin/python3` rather than Homebrew's, because `/opt/homebrew` is
  writable by your user and would otherwise make the sudoers grant equivalent to a root
  shell.

**`mc-askpass`** — reads a sudo password from the login Keychain, exported as
`SUDO_ASKPASS`. Only needed for third-party tools that call `sudo` themselves and cannot
be routed through `mc-root` — chiefly `brew upgrade --cask` for pkg-based casks. Skip it
with `./install.sh --no-askpass`; those steps are then skipped rather than hanging.

Remove everything with `./install.sh --uninstall`.

---

## Safety

### Nothing is deleted; it is moved

Above the `safe` tier, paths are **moved** to `~/.maccleaner/quarantine/<timestamp>/`
rather than deleted. On the same APFS volume that is a metadata operation, so staging
14 GB is instant.

**The honest trade-off: disk space does not come back until the batch is purged.** That
happens at the *start* of the next run, once the batch is 7 days old — which is why the
purge runs first, before anything else needs room.

```bash
mc --list-quarantine              # what is staged, how old, how big
mc --restore 2026-07-30T03-00-00  # put a batch back exactly where it came from
mc --purge-quarantine             # reclaim expired batches now
mc --no-quarantine                # delete outright instead (no undo)
```

`app_leftovers` is always quarantined regardless of that flag — it is a heuristic, and
heuristics do not get to delete things outright.

### The path policy

Everything funnels through [mc/policy.py](mc/policy.py). Three answers:

- **Hard-protected** — never deletable, by anything, with any flag. `~/Downloads`,
  SSH/GPG/AWS credentials, Keychains, iCloud Drive, Messages, any `*.gguf` /
  `*.safetensors` / `*.photoslibrary` / `*.pvm` / `*.sparsebundle` anywhere, every `.git`
  directory, `/System`, `/etc`, `/opt/homebrew`.
- **Soft-protected** — needs a module to call `.override_protection(reason)`, and the
  reason is recorded in the run report. `~/Documents`, `~/Desktop`, `~/Pictures`,
  `~/Library/Preferences`, `~/Library/Mail`.
- **Privileged allowlist** — the only paths `mc-root` will delete as root.

`mc --explain-policy` prints the lot.

Hard protection is evaluated against **every plausible home directory**, not just the
configured one, so a wrong `MACCLEANER_HOME` cannot expose real user data. That is not
theoretical: during development a home-resolution mismatch between pathlib and the policy
caused a test to stage the whole of `~/Downloads`. Nothing was lost — quarantine made it a
move, and the manifest restored it exactly — but the policy, the `Path` class and the test
suite were all changed so it cannot recur. See
`test_real_home_is_protected_even_when_policy_home_is_wrong`.

### `~/Downloads` specifically

Hard-protected, by explicit instruction. Not soft-protected — there is deliberately no
override that reaches it, `mc-root` enforces the same rule independently, and the test
suite asserts it under every combination of flags. No module references it, and
`tests/conftest.py` fails the whole run if any test so much as changes its contents.

### Other guards

- **Running-app guard** — an app's caches are not touched while it is running, unless the
  module quits it first (Teams, Telegram, Simulator).
- **Timeouts** — every command runs in its own process group under a deadline. A hung
  `brew` cannot wedge an unattended run.
- **Preflight** — aborts below a free-space floor, optionally on battery, and warns when
  Full Disk Access or the root helper is missing.
- **`--snapshot`** — takes an APFS local snapshot first. The scheduled run does this.

---

## Profiles

Risk tiers, cumulative — a profile runs its own tier and every tier below it.

| Profile | What it adds |
|---|---|
| `safe` | Throwaway data only, nothing needing root: Trash, DNS flush, Homebrew cleanup |
| `standard` | All caches and logs, dev toolchains, Electron and browser caches, `.DS_Store` |
| **`aggressive`** (default) | System `/Library` and `/var/log`, unified log, Time Machine local snapshots, Xcode DeviceSupport, Android keep-latest, orphaned app leftovers, sleep image |
| `nuclear` | Kernel extension cache, Spotlight reindex. Off by default; slow or boot-path |

```bash
mc --profile standard
mc --only xcode,homebrew        # ignores the tier filter
mc --skip dev,browsers          # names or tags
mc --tags cache
mc --list-modules
```

Make your preferences stick in `~/.maccleaner/config.toml` — useful for the scheduled
run, which has no command line:

```toml
profile = "aggressive"
skip = ["kext_cache"]
retention_days = 14
min_free_gb = 10
```

---

## The update phase

`topgrade` does the heavy lifting, driven with [config/topgrade.toml](config/topgrade.toml)
(your own `~/.config/topgrade.toml` is left alone). MacCleaner adds what topgrade misses:
`brew autoremove`/`cleanup`/`tap --repair`, oh-my-zsh, `uv tool upgrade --all`,
`pipx upgrade-all`, and the nvm upgrade flow (install latest LTS, migrate global packages,
drop the old version, repoint `default`, ensure corepack).

**macOS system updates are off by default.** `softwareupdate -ia` can reboot the machine
without warning, which an unattended weekly run has no business doing. Opt in with
`--os-updates`.

```bash
mc --no-update      # clean only — skip topgrade entirely
mc --update-only    # update only
```

The update phase runs first and can take several minutes. On a terminal, topgrade's
output is streamed live so you can see progress, and **Ctrl-C aborts it** — the child is
signalled, not just MacCleaner. If you only want to clean, `--no-update` skips it.

---

## Project cleaning

Build artefacts are project-scoped, so they get their own command rather than a module —
a scheduled cleaner should not be deciding which of your checkouts to rebuild.

```bash
mc --project-clean ~/code/myapp --dry-run
mc --project-clean ~/code/myapp
mc --project-clean ~/code/myapp --deep   # also run the package managers' own cache cleans
```

Detects the project type and removes what applies: `node_modules`, `.next`, `.turbo`,
`ios/Pods`, `android/build`, `target`, `.venv`, `__pycache__`. It refuses to run anywhere
that is not recognisably a project root, and it never runs `xcrun simctl erase all` — the
old script did, which wipes every simulator on the machine as a side effect of cleaning
one checkout.

---

## Scheduling

```bash
./install.sh --schedule
launchctl kickstart -k gui/$(id -u)/com.prabucki.maccleaner   # run it now
```

Sundays at 03:00, AC power only, with a pre-run snapshot. A LaunchAgent rather than a
daemon so it runs in your GUI session, where the login Keychain and your Full Disk Access
grant are reachable. Logs land in `~/.maccleaner/logs/`.

---

## Reports

Every run writes `~/.maccleaner/logs/<timestamp>.json` with per-module bytes, policy
denials, overrides used and errors, plus a console table and a notification.

The summary distinguishes **reclaimed** (deleted, space back now) from **staged** (moved
to quarantine, space back on purge) and reports the **measured change in free space**,
which is the only number that cannot lie.

---

## Staying current with upstream

```bash
make upstream    # git fetch upstream && git merge upstream/main
make test
```

Patches to `mac_cleanup/` are deliberately small and all marked `MacCleaner patch:`:

| File | Change |
|---|---|
| `core_modules.py` | Pluggable deletion runtime; `.privileged()` / `.quarantined()` / `.override_protection()`; expand `~` through the policy |
| `utils.py` | `cmd()` gains a timeout and kills the process group on expiry |
| `parser.py` | Do not consume `sys.argv` at import; `parse_known_args` |
| `progress.py` | Tolerate rich ≥14's `clear_live()` semantics (upstream crashes on first use) |
| `tests/test_parser.py` | Pass `args=[]` as the test always intended |

Everything else lives in `mc/`, which upstream never touches. `make test` fails if a new
upstream module appears that is neither adopted nor explicitly declined.

---

## Layout

```
mc/
  policy.py       path safety policy — the single source of truth
  runtime.py      the one place a path becomes a deletion
  quarantine.py   stage / purge / restore
  privileged.py   client for the root helper
  registry.py     module registry and the authoring DSL
  modules/        ~70 cleanup modules
  privileged/     mc-root, mc-askpass  (installed to /usr/local/libexec)
mac_cleanup/      upstream, five marked patches
config/           headless topgrade config
launchd/          weekly agent
docs/legacy/      the scripts this replaces, and the mapping
```

Writing a module:

```python
@cleanup_module(name="thing", risk=Risk.STANDARD, requires_binary=["thing"], tags=("dev",))
def thing(ctx: Context) -> None:
    with ctx.step("Cleaning thing") as step:
        step.path("~/Library/Caches/thing/*")
        step.root_path("/Library/Caches/thing/*")       # via mc-root
        step.command(["thing", "cache", "clean"], timeout=300)
        step.measure("~/somewhere/big")                  # report, never delete
```

Modules declare; they never delete, and never decide policy.

---

## Licence

Apache-2.0, inherited from mac-cleanup-py. The upstream README is preserved at
[docs/legacy/UPSTREAM_README.md](docs/legacy/UPSTREAM_README.md).
