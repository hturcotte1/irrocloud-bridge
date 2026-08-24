"""Bridge-wide error types.

The rule from the build brief: stop and alert rather than guess. ``AlertError``
is the "stop and alert" carrier — anything raising it must include a
plain-English reason Henry can read in the status email, plus whatever evidence
exists (screenshot paths, the first lines of a file that would not parse).
"""


class AlertError(Exception):
    """A condition where the bridge must stop and tell Henry, not guess.

    ``reason`` is plain English for the status email. ``evidence_lines`` are
    short text snippets (e.g. the first five lines of an unrecognized export).
    ``screenshots`` are paths to PNG files to attach to the alert email.
    """

    def __init__(
        self,
        reason: str,
        *,
        evidence_lines: list[str] | None = None,
        screenshots: list[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence_lines = evidence_lines or []
        self.screenshots = screenshots or []

    def full_text(self) -> str:
        """The reason plus evidence, formatted for an email body."""
        parts = [self.reason]
        if self.evidence_lines:
            parts.append("")
            parts.append("Evidence:")
            parts.extend(f"    {line}" for line in self.evidence_lines)
        return "\n".join(parts)


class ConfigError(Exception):
    """A setting is missing or wrong. The message must say, in plain English,
    which value to fix and where (.env or fields.json)."""
