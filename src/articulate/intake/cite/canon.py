"""Canonicalisation and normalisation for the citation check (§3.5, #35, #37)."""
import re
import unicodedata

# --- Symbol-font Private Use Area mapping (U+F0xx) ----------------------------
# PowerPoint equations pasted as Symbol text: U+F0xx -> char.
_SYM_LETTERS = dict(zip("abgdezhqiklmnxprstufcywjvJ", "αβγδεζηθικλμνξπρστυφχψωϕϖϑ"))
_SYM_LETTERS.update(zip("ABGDEZHQIKLMNXPRSTUFCYW", "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΠΡΣΤΥΦΧΨΩ"))
SYMBOL_PUA: dict[int, str] = {0xF000 + ord(k): v for k, v in _SYM_LETTERS.items()}
SYMBOL_PUA.update(
    {
        0xF0E5: "∑",
        0xF0F2: "∫",
        0xF0B4: "x",
        0xF0B1: "±",
        0xF0A3: "≤",
        0xF0B3: "≥",
        0xF0B9: "≠",
        0xF0AE: "→",
        0xF0D6: "√",
        0xF0B6: "∂",
        0xF0D7: "·",
        0xF02D: "-",
        0xF02B: "+",
        0xF03D: "=",
        0xF0BC: "…",
        0xF0A5: "∞",
        0xF0C8: "∪",
        0xF0C7: "∩",
        0xF0CE: "∈",
        0xF0C6: "∅",
    }
)

# --- Greek letter mapping ----------------------------------------------------
GREEK: dict[str, str] = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ϵ": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ϑ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "π": "pi",
    "ϖ": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "ϕ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "Γ": "gamma",
    "Δ": "delta",
    "Θ": "theta",
    "Λ": "lambda",
    "Ξ": "xi",
    "Π": "pi",
    "Σ": "sigma",
    "Φ": "phi",
    "Ψ": "psi",
    "Ω": "omega",
}

# --- Whole LaTeX commands (Issue #26/#35: whole commands only) ----------------
LATEX: dict[str, str] = {
    "le": "≤",
    "leq": "≤",
    "leqslant": "≤",
    "ge": "≥",
    "geq": "≥",
    "geqslant": "≥",
    "neq": "≠",
    "ne": "≠",
    "cup": "∪",
    "cap": "∩",
    "bigcup": "∪",
    "bigcap": "∩",
    "sum": "∑",
    "int": "∫",
    "times": "x",
    "pm": "±",
    "infty": "∞",
    "in": "∈",
    "emptyset": "∅",
    "varnothing": "∅",
    "to": "→",
    "rightarrow": "→",
    "sqrt": "√",
    "partial": "∂",
    "mid": "|",
    "vert": "|",
    "prime": "'",
    "ell": "l",
}
LATEX.update({n: n for n in set(GREEK.values())})
LATEX.update({"varphi": "phi", "varepsilon": "epsilon", "vartheta": "theta", "varpi": "pi"})

# --- Operators kept in canonical representation -------------------------------
OPS: dict[str, str] = {
    "+": "+",
    "-": "-",
    "−": "-",
    "–": "-",
    "=": "=",
    "<": "<",
    ">": ">",
    "≤": "≤",
    "≥": "≥",
    "≠": "≠",
    "∪": "∪",
    "∩": "∩",
    "∑": "∑",
    "∫": "∫",
    "√": "√",
    "!": "!",
    "|": "|",
    "±": "±",
    "∈": "∈",
}

# Boundary near math: formula delimiter, line break, or clause punctuation
BOUND: re.Pattern = re.compile(r"\\q?quad\b|\$\$|\$|\n|[.,;:?](?=\s|$)|\\\\")
MATH_EDGE: re.Pattern = re.compile(r"[0-9)!=+−\-<>≤≥≠∪∩∑∫√|/\]}^_']|[-]")
LABEL: re.Pattern = re.compile(r"^\s*(?:\(?[a-z]\)|\(?\d{1,2}[a-z]?[).]|Q\s*[.,]?\s*\d+\s*[.:]?)\s*", re.IGNORECASE)

# PUA letters can match either plain ASCII letter or Greek name
PUA_LETTER: dict[int, tuple[str, str]] = {
    0xF000 + ord(k): (k.lower(), GREEK[v]) for k, v in _SYM_LETTERS.items() if v in GREEK
}


def latex_plain(t: str) -> str:
    """Convert LaTeX commands to plain text tokens by whole command (§3.5, #35)."""
    # Strip formatting commands keeping inner content
    t = re.sub(
        r"\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname)\s*\{([^}]*)\}",
        r" \1 ",
        t,
    )
    # Replace whole commands only (prevents \cdot from eating \cdots)
    t = re.sub(r"\\([a-zA-Z]+)", lambda m: f" {LATEX.get(m.group(1), '')} ", t)
    # Spacing commands
    t = re.sub(r"\\[,;:! ]", " ", t)
    # Strip LaTeX delimiters and group braces
    return re.sub(r"[\\{}$_^]", " ", t)


def quote_dialect(t: str) -> str:
    """Normalize Gemini linear math dialect to standard tokens (§3.5, #35)."""
    # ∑(i=1 to k) -> ∑ i=1 k
    t = re.sub(r"\(\s*(\w+)\s*=\s*(\w+)\s+to\s+(\w+)\s*\)", r" \1=\2 \3 ", t)
    t = re.sub(r"\bsqrt\b", "√", t)
    return t


def canon(t: str, ops: bool = True) -> str:
    """Canonicalize text string into comparison tokens (§3.5, #35)."""
    t = "".join(c if ord(c) in PUA_LETTER else SYMBOL_PUA.get(ord(c), c) for c in t).replace("×", "x")
    t = unicodedata.normalize("NFKC", t)
    t = "".join(f" {GREEK[c]} " if c in GREEK else c for c in t).lower()

    if ops:
        keep = lambda c: (c.isascii() and c.isalnum()) or c in OPS or ord(c) in PUA_LETTER
    else:
        keep = lambda c: (c.isascii() and c.isalnum()) or ord(c) in PUA_LETTER

    return "".join(OPS.get(c, c) for c in t if keep(c))


def q_canon(q: str, ops: bool = True) -> str:
    """Canonicalize a quote string."""
    return canon(quote_dialect(latex_plain(q)), ops)


def v_canon(v: str, ops: bool = True) -> str:
    """Canonicalize a transcript / page text string."""
    return canon(latex_plain(v), ops)


def chunks_canon(v: str, ops: bool = True) -> tuple[str, set[int]]:
    """
    Canonicalize page text split by formula/line/clause boundaries,
    returning the concatenated canonical string and the set of cut offsets.
    """
    parts = [canon(latex_plain(c), ops) for c in BOUND.split(v)]
    s = ""
    cuts = {0}
    n = 0
    for p in parts:
        s += p
        n += len(p)
        cuts.add(n)
    return s, cuts


def pua_regex(n: str) -> re.Pattern:
    """Generate regex pattern allowing PUA letters to match either plain letter or Greek name."""
    pattern_parts = []
    for c in n:
        code = ord(c)
        if code in PUA_LETTER:
            letter, greek_name = PUA_LETTER[code]
            pattern_parts.append(f"(?:{re.escape(greek_name)}|{re.escape(letter)})")
        else:
            pattern_parts.append(re.escape(c))
    return re.compile("".join(pattern_parts))
