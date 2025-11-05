# Copyright The Linux Foundation and each contributor to LFX.
# SPDX-License-Identifier: MIT

# Special thanks to <https://gist.github.com/nymous/f138c7f06062b7c43c060bf03759c29e>
# Copyright (c) 2023 Thomas GAUDIN
# SPDX-License-Identifier: MIT

import logging
import sys
from types import TracebackType

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# Log message attribute for JSON logging.
MESSAGE_LOG_ATTRIBUTE = "message"

# https://gist.github.com/nymous/f138c7f06062b7c43c060bf03759c29e
# Distributed under the MIT License


def rename_event_key(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Rename "event" key to "message" for JSON ingest compatibility."""
    event_dict[MESSAGE_LOG_ATTRIBUTE] = event_dict.pop("event")
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Set up JSON or pretty logging based on whether or not this is a TTY."""
    console_timestamper = structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S")
    iso_timestamper = structlog.processors.TimeStamper(fmt="iso")
    # Shared processors will be used by logging entries that originate from
    # either `logging` or `structlog`.
    shared_processors: list[Processor] = [
        # Add log level to event dict.
        structlog.stdlib.add_log_level,
        # Perform %-style formatting.
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Add extra attributes of LogRecord objects to the event dictionary
        # so that values passed in the extra parameter of log methods pass
        # through to log output.
        structlog.stdlib.ExtraAdder(),
    ]
    if sys.__stdout__ is not None and sys.__stdout__.isatty():
        # Set our renderer for ProcessorFormatter.
        log_renderer: Processor = structlog.dev.ConsoleRenderer()
        # Add TTY processors.
        shared_processors.append(console_timestamper)
    else:
        # Set our renderer for ProcessorFormatter.
        log_renderer = structlog.processors.JSONRenderer()
        # Add JSON processors.
        shared_processors.append(iso_timestamper)
        # Only rename the message key for JSON, because "event" is expected by
        # ConsoleRenderer.
        shared_processors.append(rename_event_key)
        # If the "stack_info" key in the event dict is true, remove it and
        # render the current stack trace in the "stack" key.
        shared_processors.append(structlog.processors.StackInfoRenderer())
        # If the "exc_info" key in the event dict is either true or a
        # sys.exc_info() tuple, remove "exc_info" and render the exception
        # with traceback into the "exception" key.
        shared_processors.append(structlog.processors.format_exc_info)
        # If some value is in bytes, decode it to a Unicode str.
        shared_processors.append(structlog.processors.UnicodeDecoder())

    structlog.configure(
        processors=shared_processors
        + [
            # If log level is too low, abort pipeline and throw away log entry.
            structlog.stdlib.filter_by_level,
            # Prepare event dict for `ProcessorFormatter`.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        # `wrapper_class` is the bound logger that you get back from
        # get_logger(). This one imitates the API of `logging.Logger`.
        wrapper_class=structlog.stdlib.BoundLogger,
        # `logger_factory` is used to create wrapped loggers that are used for
        # OUTPUT. This one returns a `logging.Logger`. The final value (a JSON
        # string) from the final processor (`JSONRenderer`) will be passed to
        # the method of the same name as that you've called on the bound
        # logger.
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Effectively freeze configuration after creating the first bound
        # logger.
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # These run ONLY on `logging` entries that do NOT originate within
        # structlog.
        foreign_pre_chain=shared_processors,
        # These run on ALL entries after the pre_chain is done.
        processors=[
            # Remove _record & _from_structlog.
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            log_renderer,
        ],
    )

    handler = logging.StreamHandler()
    # Use OUR `ProcessorFormatter` to format all `logging` entries.
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        """Log uncaught exceptions to the root logger.

        Log any uncaught exception instead of letting it be printed by Python
        (but leave KeyboardInterrupt untouched to allow users to Ctrl+C to
        stop).
        """
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        root_logger.error(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = handle_exception
