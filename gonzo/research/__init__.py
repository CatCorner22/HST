"""The Morgue: a sourced research desk about Hunter S. Thompson."""

from gonzo.research.morgue import (
    QUOTE_WORD_LIMIT,
    load_fact_file,
    load_quote_bank,
    morgue_system_prompt,
    quote_word_count,
)

__all__ = [
    "QUOTE_WORD_LIMIT",
    "load_fact_file",
    "load_quote_bank",
    "morgue_system_prompt",
    "quote_word_count",
]
