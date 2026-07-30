"""All core modules."""

from abc import ABC, abstractmethod
from pathlib import Path as Path_
from typing import Any, Final, Optional, TypeVar, final

from beartype import beartype  # pyright: ignore [reportUnknownVariableType]

from mac_cleanup import args
from mac_cleanup.progress import ProgressBar
from mac_cleanup.utils import check_deletable, check_exists, cmd

T = TypeVar("T")


# --------------------------------------------------------------------------------------
# MacCleaner patch: pluggable execution runtime
#
# Upstream deletes with a bare `rm -rf` and refuses anything under /Library, /System,
# /usr or /Applications via check_deletable(). MacCleaner needs three extra behaviours —
# deletion as root, staging into quarantine instead of deleting, and its own path policy.
#
# Rather than rewrite the module classes, a runtime object can be installed here. When
# one is present, Path delegates its deletion decision to it; when absent, upstream
# behaviour is bit-for-bit unchanged, so `mac-cleanup` still works as it always did.
# --------------------------------------------------------------------------------------

_runtime: Optional[Any] = None
_current_module: str = "upstream"


def set_runtime(runtime: Optional[Any]) -> None:
    """Install (or clear, with None) the object that performs deletions."""

    global _runtime
    _runtime = runtime


def get_runtime() -> Optional[Any]:
    """The currently installed execution runtime, if any."""

    return _runtime


def set_current_module(name: str) -> None:
    """
    Tag subsequently-constructed modules with the cleanup module that created them.

    Modules are constructed during collection but executed later, so the association has
    to be captured at construction time for per-module policy and reporting to work.
    """

    global _current_module
    _current_module = name


def get_current_module() -> str:
    """Name of the cleanup module currently being collected."""

    return _current_module


def _expand_user(path: str) -> str:
    """
    Expand ``~`` the same way the policy does.

    Imported lazily so plain upstream usage (``mac-cleanup`` with no ``mc`` on the path)
    still works; it falls back to pathlib's behaviour, which is what upstream always did.
    """

    try:
        from mc.policy import expand
    except ImportError:  # pragma: no cover - upstream-only installation
        return Path_(path).expanduser().as_posix()

    return expand(path)


class BaseModule(ABC):
    """Base abstract module."""

    __prompt: bool = False
    __prompt_message: str = "Do you want to proceed?"

    @beartype
    def with_prompt(self: T, message_: Optional[str] = None) -> T:
        """
        Execute command with user prompt.

        :param message_: Message to be shown on prompt
        :return: Instance of self from
        :class: `BaseModule`
        """

        if args.force:
            return self

        # Can't be solved without typing.Self
        self.__prompt = True  # pyright: ignore [reportAttributeAccessIssue]

        if message_:
            # Can't be solved without typing.Self
            self.__prompt_message = message_  # pyright: ignore [reportAttributeAccessIssue]

        return self

    @abstractmethod
    def _execute(self) -> bool:
        """Base exec with check for prompt :return: True on successful prompt."""

        # Call prompt if needed
        if self.__prompt:
            # Skip on negative prompt
            return ProgressBar.prompt(prompt_text=self.__prompt_message, prompt_title="Module requires attention")

        return True


class _BaseCommand(BaseModule):
    """Base Command with basic command methods."""

    @beartype
    def __init__(self, command_: Optional[str]):
        self.__command: Final[Optional[str]] = command_

        # MacCleaner patch: remember which cleanup module built this, for reporting.
        self.owner: str = get_current_module()

    @property
    def get_command(self) -> Optional[str]:
        """Get command specified to the module."""

        return self.__command

    @abstractmethod
    def _execute(self, ignore_errors: bool = True) -> Optional[str]:
        """
        Execute the command specified.

        :param ignore_errors: Ignore errors during execution
        :return: Command execution results based on specified parameters
        """

        # Skip if there is no command
        if not self.__command:
            return

        # Skip on negative prompt
        if not super()._execute():
            return

        # Execute command
        return cmd(command=self.__command, ignore_errors=ignore_errors)


@final
class Command(_BaseCommand):
    """Collector list unit for command execution."""

    __ignore_errors: bool = True

    def with_errors(self) -> "Command":
        """Return errors in exec output :return: :class:`Command`"""

        self.__ignore_errors = False

        return self

    def _execute(self, ignore_errors: Optional[bool] = None) -> Optional[str]:
        """
        Execute the command specified.

        :param ignore_errors: Overrides flag `ignore_errors` in class
        :return: Command execution results based on specified parameters
        """

        return super()._execute(ignore_errors=self.__ignore_errors if ignore_errors is None else ignore_errors)


@final
class Path(_BaseCommand):
    """Collector list unit for cleaning paths."""

    __dry_run_only: bool = False

    # MacCleaner patch: deletion strategy flags, set through the builder methods below.
    __privileged: bool = False
    __quarantine: Optional[bool] = None
    __override: bool = False
    __override_reason: str = ""

    @beartype
    def __init__(self, path: str):
        # MacCleaner patch: expand "~" through mc.policy, not pathlib.
        #
        # pathlib's expanduser() resolves against the process's real HOME, while the
        # policy resolves against its own notion of the target user (honouring SUDO_USER,
        # and overridable for tests). When those two disagree, the path stored here and
        # the path the policy checks are different strings, so a protected location can
        # pass the check under a home the policy is not looking at. Routing both through
        # one function is what makes the protection sound.
        self.__path: Final[Path_] = Path_(_expand_user(path))

        tmp_command = "rm -rf '{path}'".format(path=self.__path.as_posix())

        super().__init__(command_=tmp_command)

    @property
    def get_path(self) -> Path_:
        """Get path specified to the module."""

        return self.__path

    def dry_run_only(self) -> "Path":
        """Set module to only count size in dry runs :return: :class:`Path`"""

        self.__dry_run_only = True

        return self

    # -- MacCleaner builder methods ----------------------------------------------------

    def privileged(self) -> "Path":
        """
        Route this deletion through the root helper.

        Required for anything the user does not own — ``/Library/Caches``,
        ``/private/var/log`` and friends. The path still has to pass the privileged
        allowlist in ``mc.policy``, which is re-checked inside the helper itself.
        """

        self.__privileged = True

        return self

    def quarantined(self, enabled: bool = True) -> "Path":
        """
        Force staging into quarantine (or force a direct delete with ``False``).

        Left unset, the run's default applies: staged for every tier above ``safe``.
        """

        self.__quarantine = enabled

        return self

    def override_protection(self, reason: str) -> "Path":
        """
        Opt past *soft* protection for this path.

        Soft-protected locations are real user data with one narrow legitimate cleanup
        case each — stale installers in ``~/Downloads``, orphaned plists in
        ``~/Library/Preferences``. Hard-protected paths cannot be reached this way.

        :param reason: Recorded in the run report so every override is auditable.
        """

        self.__override = True
        self.__override_reason = reason

        return self

    @property
    def is_privileged(self) -> bool:
        return self.__privileged

    @property
    def quarantine_preference(self) -> Optional[bool]:
        return self.__quarantine

    @property
    def has_override(self) -> bool:
        return self.__override

    @property
    def override_reason(self) -> str:
        return self.__override_reason

    @property
    def is_dry_run_only(self) -> bool:
        return self.__dry_run_only

    # ----------------------------------------------------------------------------------

    def _execute(self, ignore_errors: bool = True) -> Optional[str]:
        """Delete specified path :return: Command execution results based on specified
        parameters.
        """

        if self.__dry_run_only:
            return

        # MacCleaner patch: hand the decision to the installed runtime, which applies
        # mc.policy, quarantine staging and root escalation. Falls through to upstream
        # behaviour when no runtime is installed.
        runtime = get_runtime()
        if runtime is not None:
            # BaseModule._execute is the prompt gate; call it directly rather than via
            # _BaseCommand, which would also run the `rm -rf` we are replacing.
            if not BaseModule._execute(self):
                return
            return runtime.delete_path(self)

        # Skip if path is not deletable or undefined
        if not all([check_deletable(path=self.__path), check_exists(path=self.__path, expand_user=False)]):
            return

        return super()._execute(ignore_errors=ignore_errors)
