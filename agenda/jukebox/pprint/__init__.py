"""Rich console display utilities for jukebox scoring and selection."""

import datetime
from typing import List, Optional, Tuple

from portion import Interval
from rich.console import Console
from rich.table import Table
from rich.text import Text

from fk.models import Video


def fmt_score(val: float) -> Text:
    """Format a score value with color: positive=green (boost), negative=red (penalty), zero=neutral.

    Args:
        val: Score value to format

    Returns:
        Rich Text object with appropriate styling
    """
    if val > 0:
        return Text(f"{val:.4f}", style="bold green")
    if val < 0:
        return Text(f"{val:.4f}", style="bold red")
    return Text(f"{val:.4f}")


def render_candidates_table(
    scored_candidates: List[Tuple[Video, float, List[Tuple[str, float]]]],
    scorer_names: List[str],
    window: Interval,
    total_fitting: int,
    console: Optional[Console] = None,
) -> None:
    """Render a Rich table showing scored candidates with per-scorer breakdowns and log diagnostics.

    Args:
        scored_candidates: List of (video, total_score, [(scorer_name, score), ...])
        scorer_names: List of scorer class names for column headers
        window: Gap window being filled
        total_fitting: Total number of fitting candidates
        console: Optional Rich Console instance (creates one if not provided)
    """
    import logging

    logger = logging.getLogger("agenda.jukebox.pprint")
    logger.info("Gap %s: %d fitting candidates", window, total_fitting)

    # Use provided console or create a default one
    if console is None:
        console = Console(width=120, force_terminal=True, color_system="truecolor")

    # Build the table
    table = Table(
        title=(
            f"Candidates at {window.lower.strftime('%Y-%m-%d %H:%M')} -> {window.upper.strftime('%Y-%m-%d %H:%M')} "
            f"({total_fitting} fitting)\n"
            "Legend: green=boost, red=penalty"
        )
    )

    # Add columns
    table.add_column("#", style="bold", justify="right")
    table.add_column("video_id", justify="right")
    table.add_column("duration(min)", justify="right")
    table.add_column("total", justify="right")

    # Add dynamic scorer columns
    for scorer_name in scorer_names:
        table.add_column(scorer_name, justify="right")

    # Add rows for top N candidates
    top_n = min(5, len(scored_candidates))
    for i in range(top_n):
        video, total, per_scorer = scored_candidates[i]

        # Calculate duration in minutes
        dur_min = getattr(video.duration, "total_seconds", lambda: 0.0)() / 60.0

        # Build row
        row = [
            str(i + 1),
            str(getattr(video, "id", "?")),
            f"{dur_min:.3f}",
            fmt_score(total),
        ]

        # Map per-scorer values for the row
        if per_scorer:
            contrib_map = {name: val for name, val in per_scorer}
            for scorer_name in scorer_names:
                row.append(fmt_score(contrib_map.get(scorer_name, 0.0)))

        # Ensure all elements are str, None, or Rich Text
        safe_row = [x if isinstance(x, (str, type(None), Text)) else str(x) for x in row]
        table.add_row(*safe_row)

    # Print the table
    console.print(table)
