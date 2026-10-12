"""One source of truth for team names.

Why this file exists: PitchIQ talks to four systems and each spells teams differently.

    football-data.co.uk (our database)   Man United   Tottenham
    Fantasy Premier League               Man Utd       Spurs
    News articles                        Manchester United, Spurs ...
    People / the LLM                     "Man U", "Tottenham Hotspur" ...

If each module invents its own spelling, lookups silently fail ("Unknown team 'Spurs'").
So EVERY module converts names with `canonical()` first. The canonical spelling is the
football-data.co.uk one, because that is what the database and the predictor use.

Newly promoted club missing from the list? Add one line to TEAMS below.
"""
import difflib
import re
from functools import lru_cache
from typing import Iterable, Optional

# canonical name -> other spellings. (All matching ignores case, punctuation and a trailing "FC".)
TEAMS: dict[str, list[str]] = {
    "Arsenal": ["gunners"],
    "Aston Villa": ["villa"],
    "Bournemouth": ["afc bournemouth"],
    "Brentford": [],
    "Brighton": ["brighton and hove albion", "brighton hove albion"],
    "Burnley": [],
    "Chelsea": [],
    "Crystal Palace": ["palace"],
    "Everton": [],
    "Fulham": [],
    "Leeds": ["leeds united"],
    "Liverpool": [],
    "Man City": ["manchester city", "mancity", "man c"],
    "Man United": ["manchester united", "man utd", "man u", "manutd"],
    "Newcastle": ["newcastle united", "nufc"],
    "Nott'm Forest": ["nottingham forest", "nottm forest", "nott forest", "forest"],
    "Sunderland": [],
    "Tottenham": ["tottenham hotspur", "spurs", "thfc"],
    "West Ham": ["west ham united", "whu"],
    "Wolves": ["wolverhampton", "wolverhampton wanderers"],
    # Clubs from recent seasons, still present in a 10-season database
    "Leicester": ["leicester city"],
    "Southampton": [],
    "Ipswich": ["ipswich town"],
    "Luton": ["luton town"],
    "Sheffield United": ["sheffield utd"],
    "Watford": [],
    "Norwich": ["norwich city"],
    "West Brom": ["west bromwich albion"],
    "Cardiff": ["cardiff city"],
    "Huddersfield": ["huddersfield town"],
    "Swansea": ["swansea city"],
    "Stoke": ["stoke city"],
    "Hull": ["hull city"],
    "Middlesbrough": ["boro"],
    "QPR": ["queens park rangers"],
}

# Fine for matching a typed team name, but too ambiguous to look for inside article text
# ("Buckingham Palace", "the Villa", "Forest fires"...).
_NAME_ONLY = {"forest", "palace", "villa", "man u", "man c", "boro", "gunners"}


def normalize(text: str) -> str:
    """'Nott'm Forest FC' -> 'nottm forest'; "Arsenal's" -> 'arsenal'."""
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[’']s\b", "", text)          # possessives
    text = re.sub(r"[^a-z0-9 ]", "", text)       # punctuation, apostrophes
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" fc$", "", text)


_ALIAS_TO_CANON: dict[str, str] = {}
for _canon, _aliases in TEAMS.items():
    for _name in [_canon, *_aliases]:
        _ALIAS_TO_CANON[normalize(_name)] = _canon


def canonical(name: str, known: Optional[Iterable[str]] = None) -> Optional[str]:
    """Return the canonical team name, or None if we cannot tell.

    `known` (optional) = team names that exist in some dataset (for example the database).
    It lets us still resolve clubs that are not in TEAMS yet."""
    key = normalize(name or "")
    if not key:
        return None
    if key in _ALIAS_TO_CANON:
        return _ALIAS_TO_CANON[key]
    if known:
        by_norm = {normalize(k): k for k in known}
        if key in by_norm:
            return by_norm[key]
        close = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=0.85)
        if close:
            return by_norm[close[0]]
    close = difflib.get_close_matches(key, list(_ALIAS_TO_CANON), n=1, cutoff=0.88)
    return _ALIAS_TO_CANON[close[0]] if close else None


def require_canonical(name: str, known: Optional[Iterable[str]] = None) -> str:
    """Like canonical(), but raises a helpful error that an LLM (or a person) can act on."""
    found = canonical(name, known)
    if found:
        return found
    pool = list(_ALIAS_TO_CANON) + [normalize(k) for k in (known or [])]
    close = difflib.get_close_matches(normalize(name), pool, n=3, cutoff=0.5)
    suggestions = sorted({_ALIAS_TO_CANON.get(c, c) for c in close})
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise ValueError(f"Unknown team '{name}'.{hint}")


@lru_cache(maxsize=1)
def _text_pattern() -> re.Pattern:
    names = sorted((n for n in _ALIAS_TO_CANON if n not in _NAME_ONLY), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b")


def find_teams_in_text(text: str) -> set[str]:
    """Which clubs does this article mention? Whole words only, so 'Fulham' matches but
    'Fulhamish' does not."""
    return {_ALIAS_TO_CANON[m] for m in _text_pattern().findall(normalize(text))}