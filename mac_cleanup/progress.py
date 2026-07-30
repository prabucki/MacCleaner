"""Modified rich progress bar."""

from typing import Iterable, Optional, Sequence

from rich.progress import (
    BarColumn,
    Progress,
    ProgressType,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm

from mac_cleanup.console import console, print_panel


def _clear_live(target_console) -> None:
    """
    Drop any active Live display, tolerating rich's changed semantics.

    MacCleaner patch. Up to rich 13, ``Console.clear_live()`` unconditionally reset a
    single ``_live`` attribute. From rich 14 it pops an internal stack and raises
    ``IndexError`` when that stack is empty — which it always is on the first call, so
    upstream crashes on the first ``wrap_iter``. Guarding here rather than pinning rich
    to 13.x keeps the fork working on current rich.
    """

    try:
        target_console.clear_live()
    except (IndexError, AttributeError):
        pass


class _ProgressBar:
    """Proxy rich progress bar with blocking prompt."""

    def __init__(self):
        # Call parent init w/ default stuff
        self.current_progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(show_speed=True),
            TimeRemainingColumn(elapsed_when_finished=True),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )

    def prompt(
        self,
        prompt_text: str,
        prompt_title: str,
        password: bool = False,
        choices: Optional[list[str]] = None,
        show_default: bool = True,
        show_choices: bool = True,
        default: bool = True,
    ) -> bool:
        """
        Stops progress bar to show prompt to user.

        :param prompt_text: Text to be shown in panel
        :param prompt_title: Title of panel to be shown
        :param password: Enable password input. Defaults to False.
        :param choices: A list of valid choices. Defaults to None.
        :param show_default: Show default in prompt. Defaults to True.
        :param show_choices: Show choices in prompt. Defaults to True.
        :param default: Default value in prompt.
        :return: True on successful prompt
        """

        # Stop refreshing progress bar
        self.current_progress.stop()

        # Print prompt to user
        print_panel(text=prompt_text, title=prompt_title)

        # Get user input
        answer = Confirm.ask(
            prompt="Do you want to continue?",
            console=self.current_progress.console,
            password=password,
            choices=choices,
            show_default=show_default,
            show_choices=show_choices,
            default=default,
        )

        # Clear printed stuff
        self.current_progress.console.clear()
        _clear_live(self.current_progress.console)

        # Resume refreshing progress bar
        self.current_progress.start()

        # Return user answer
        return answer

    def wrap_iter(
        self,
        sequence: Iterable[ProgressType] | Sequence[ProgressType],
        total: Optional[float] = None,
        description: str = "Working...",
    ) -> Iterable[ProgressType]:
        """
        Wrapper other :func:`rich.progress.track`

        :param sequence: Sequence (must support "len") you wish to iterate over.
        :param total: Total number of steps. Default is len(sequence).
        :param description: Description of task show next to progress bar. Defaults to "Working".
        :return: An iterable of the values in the sequence
        """

        # Clear previous Live instance
        _clear_live(self.current_progress.console)

        # Get new progress instance with default stuff
        self.__init__()

        # Call context manager and yield from it
        with self.current_progress:
            yield from self.current_progress.track(sequence, total=total, description=description)


# ProgressBar instance for all project
ProgressBar = _ProgressBar()
