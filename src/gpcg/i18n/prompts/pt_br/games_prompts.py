"""pt-BR games prompts — re-exports from the canonical location.

The canonical prompt definitions live in ``gpcg.domains.games.prompts``.
This module re-exports them so that ``PromptRegistry`` can find the pt-BR
pack at ``gpcg.i18n.prompts.pt_br.games_prompts``.

When a new language is added, create a new module (e.g. ``en_us/games_prompts.py``)
with translated prompt constants.
"""

from gpcg.domains.games.prompts import *  # noqa: F401,F403
