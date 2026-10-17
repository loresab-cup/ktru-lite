"""KTRU catalog in memory and the search of candidate codes.

The dump (JSONL in gzip) is streamed once into an (n x 1024) float32 matrix; the matrix and
the short metadata are cached in CACHE_DIR, so later starts take seconds.
Search: exact cosine (queries @ matrix.T) -> 50 nearest -> + WORD_BONUS per shared word
(name + characteristic values: "А4", "80 г/м2" pick the code inside a group) -> top-5.
Exact, not HNSW: on this catalog HNSW loses up to 14 % of the right codes.
"""

import base64
import gzip
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(os.getenv("CATALOG_PATH", "/catalog/ktru_catalog.jsonl.gz"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/cache"))
DIM = 1024
VECTOR_LIMIT = 50
WORD_BONUS = 0.02
TOP_K = 5
QUERY_CHUNK = 128  # 128 x 81 509 float32 = 42 MB of similarities at a time
WORDS_ONLY_BASE = 0.6  # without vectors the score stays low: nothing goes to auto
MAX_CODE_WORDS = 400
BRIEF_CHARS, BRIEF_VALUES = 12, 20
CACHE_VERSION = 5  # bump when the cached metadata changes
# Neighbour codes of one group often differ only by a range: "> 1.25" vs "<= 1.25".
# A bound of the row equal to a bound of the code adds NUMERIC_BONUS; the same number on the
# other side ("не менее 1.25" against "<= 1.25") takes NUMERIC_CLASH off.
NUMERIC_BONUS = 0.01
NUMERIC_CLASH = 0.01
# Neighbour codes also differ by a fixed characteristic («Неизменяемая заказчиком») with one
# value: «Вид сырья: Коровье молоко» vs «Козье молоко», «Для детского питания: Да» vs «Нет».
#   FIXED_BONUS - a fixed value of the code is written in the row;
#   FIXED_CLASH - the row names the characteristic, but with another value;
#   FIXED_FLAG  - the code has a special «Да» feature the row does not mention.
# Checked on the real specification (12 rows: 9 -> 10 right) and on the draft gold
# (291 rows: 66.7 -> 68.7 %).
FIXED_BONUS = 0.02
FIXED_CLASH = 0.04
FIXED_FLAG = 0.03
MAX_FIXED_VALUES = 30
# A template («шаблон») lists every value of its group, so it matches any row as well as the
# right specific code. In a real specification the specific code is chosen (12 of 12 there):
# a template is a little lower - less than one shared word, so it wins only when no specific
# code fits the row as well.
TEMPLATE_PENALTY = 0.015
# Words of the code name that the row does not have: «Мясо птицы охлажденное для детского
# питания» must lose to «Мясо птицы охлажденное» for a row about chickens, not baby food.
# A/B on stored rows: no change on the draft gold (73.9 %) and the real file (12/12); it
# fixes the near tie of such codes when the network is down and the row itself is the query.
NAME_EXTRA_PENALTY = 0.01
# Twin codes differ only by the unit: «Кефир» in litres and in kilograms. The unit of the row
# (the «Ед. изм.» column, else the unit the product is usually bought in) decides such a tie;
# the bonus is smaller than any other rule, so it never overrules them.
UNIT_BONUS = 0.005

# Lowercased unit -> key; full names by prefix (longer first), short forms exactly
_UNIT_PREFIXES = (
    ("квадратный метр", "m2"),
    ("кубический метр", "m3"),
    ("кубический дециметр", "l"),
    ("миллилитр", "ml"),
    ("литр", "l"),
    ("килограмм", "kg"),
    ("грамм", "g"),
    ("тонн", "t"),
    ("штук", "pcs"),
    ("упаков", "pack"),
    ("комплект", "set"),
    ("пар", "pair"),
    ("человеко-час", "manhour"),
    ("условн", "unit"),
    ("метр", "m"),
)
_UNIT_SHORT = {
    "кг": "kg",
    "г": "g",
    "гр": "g",
    "т": "t",
    "л": "l",
    "мл": "ml",
    "дм3": "l",
    "шт": "pcs",
    "упак": "pack",
    "уп": "pack",
    "компл": "set",
    "к-т": "set",
    "пара": "pair",
    "м": "m",
    "м2": "m2",
    "м3": "m3",
    "чел.-ч": "manhour",
    "чел.-час": "manhour",
    "усл. ед": "unit",
    "усл.ед": "unit",
}

# Latin letters that look like Cyrillic ones: "A4" and "А4" must be the same word
_LOOKALIKE = str.maketrans("aceopxkmhbty", "асеорхкмнвту")
_WORD = re.compile(r"[a-zа-я0-9]+")
_NUM = r"(\d+(?:[.,]\d+)?)"
_LOWER = re.compile(
    rf"(?<!\w)(?:не\s+менее|не\s+меньше|(?<!не )более|(?<!не )больше|свыше|от|≥|>=|>)\s*{_NUM}"
)
_UPPER = re.compile(
    rf"(?<!\w)(?:не\s+более|не\s+больше|(?<!не )менее|(?<!не )меньше|до|≤|<=|<)\s*{_NUM}"
)


def words(text: str) -> set[str]:
    """Words for matching: 3+ letters or with a digit, lowercase, ё=е, Latin lookalikes."""
    text = (text or "").lower().replace("ё", "е").translate(_LOOKALIKE)
    return {w for w in _WORD.findall(text) if len(w) >= 3 or any(c.isdigit() for c in w)}


def query_bounds(text: str) -> tuple[set[float], set[float]]:
    """Lower and upper bounds written in a row: "не менее 3000 и менее 4000" -> {3000}, {4000}."""
    text = (text or "").lower().replace("ё", "е")
    lower = {float(m.group(1).replace(",", ".")) for m in _LOWER.finditer(text)}
    upper = {float(m.group(1).replace(",", ".")) for m in _UPPER.finditer(text)}
    return lower, upper


def unit_key(text: str) -> str | None:
    """«Литр», «л», «кубический дециметр» -> "l"; unknown units -> None."""
    text = " ".join((text or "").lower().replace("ё", "е").split()).strip(" .")
    if text in _UNIT_SHORT:
        return _UNIT_SHORT[text]
    return next((key for prefix, key in _UNIT_PREFIXES if text.startswith(prefix)), None)


def stems(text: str) -> set[str]:
    """First 5 letters of the words: «коровье» and «коровьего» are the same."""
    return {word[:5] for word in words(text)}


@dataclass
class Catalog:
    vectors: np.ndarray  # (n, DIM) float32, L2-normalized
    codes: list[str]
    statuses: list[str]
    names: list[str]
    okpd2: list[str]
    code_words: list[str]  # space-joined words of name + characteristic values
    chars: list[list]  # [[name, [values]]] short, for the UI
    bounds: list[list]  # [[min values], [max values]] of the numeric characteristic values
    fixed: list[list]  # [[name, [values]]] of the fixed characteristics
    templates: list[bool]  # is_template: the general code of a group
    units: list[list[str]]  # unit keys of the code: ["l"], ["kg"]
    index: dict[str, int] = field(init=False)
    active: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.index = {code: i for i, code in enumerate(self.codes)}
        self.active = np.array([s == "ACTIVE" for s in self.statuses], dtype=bool)
        self._names_lower = [name.lower().replace("ё", "е") for name in self.names]
        self._postings: dict[str, np.ndarray] | None = None
        self._lock = threading.Lock()

    # --- search ----------------------------------------------------------------------------

    def search(
        self,
        vectors: np.ndarray | None,
        texts: list[str],
        include_inactive: bool = False,
        units: list[str | None] | None = None,
    ) -> list[list[dict]]:
        """Top-5 candidates for every text; vectors=None -> by words only."""
        query_words = [words(text) for text in texts]
        if vectors is not None:
            nearest = self._nearest_by_vectors(vectors, include_inactive)
        else:
            nearest = [self._nearest_by_words(qw, include_inactive) for qw in query_words]
        return [
            self._rank(qw, query_bounds(text), stems(text), near, unit)
            for qw, text, near, unit in zip(
                query_words, texts, nearest, units or [None] * len(texts), strict=True
            )
        ]

    def _nearest_by_vectors(self, queries: np.ndarray, include_inactive: bool) -> list[list]:
        limit = min(VECTOR_LIMIT, len(self.codes))
        result = []
        for start in range(0, len(queries), QUERY_CHUNK):
            sims = queries[start : start + QUERY_CHUNK] @ self.vectors.T
            if not include_inactive:
                sims[:, ~self.active] = -np.inf
            top = np.argpartition(-sims, limit - 1, axis=1)[:, :limit]
            for row, idx in zip(sims, top, strict=True):
                result.append([(int(i), float(row[i])) for i in idx if np.isfinite(row[i])])
        return result

    def _nearest_by_words(self, query_words: set[str], include_inactive: bool) -> list:
        postings = self._name_postings()
        counts = np.zeros(len(self.codes), dtype=np.int32)
        for word in query_words:
            if word in postings:
                counts[postings[word]] += 1
        if not include_inactive:
            counts[~self.active] = 0
        top = np.argsort(-counts, kind="stable")[:VECTOR_LIMIT]
        total = max(1, len(query_words))
        return [(int(i), WORDS_ONLY_BASE * counts[i] / total) for i in top if counts[i] > 0]

    def _name_postings(self) -> dict[str, np.ndarray]:
        """word -> indices of codes with it in the name; built on the first words-only search."""
        with self._lock:
            if self._postings is None:
                lists: dict[str, list[int]] = {}
                for i, name in enumerate(self.names):
                    for word in words(name):
                        lists.setdefault(word, []).append(i)
                self._postings = {w: np.array(ix, dtype=np.int32) for w, ix in lists.items()}
            return self._postings

    def _numeric_fit(self, i: int, query: tuple[set[float], set[float]]) -> tuple[int, int]:
        """(bounds of the row the code has, bounds the code has only on the other side)."""
        lower, upper = query
        code_lower, code_upper = (set(values) for values in self.bounds[i])
        matches = len(lower & code_lower) + len(upper & code_upper)
        clashes = len((lower & code_upper) - code_lower) + len((upper & code_lower) - code_upper)
        return matches, clashes

    def _fixed_fit(self, i: int, query_stems: set[str]) -> tuple[int, int, int]:
        """(fixed values written in the row, fixed values the row contradicts, unasked «Да»)."""
        matches = clashes = flags = 0
        for name, values in self.fixed[i]:
            name_stems = stems(name)
            named = len(name_stems & query_stems) >= max(1, len(name_stems) // 2)
            if len(values) == 1 and values[0].lower() in ("да", "нет"):
                flags += values[0].lower() == "да" and not named
                continue
            if any(stems(value) and stems(value) <= query_stems for value in values):
                matches += 1
            elif named:
                clashes += 1
        return matches, clashes, flags

    def _rank(
        self,
        query_words: set[str],
        query: tuple[set[float], set[float]],
        query_stems: set[str],
        nearest: list,
        unit: str | None = None,
    ) -> list[dict]:
        scored = []
        for i, cosine in nearest:
            shared = query_words & set(self.code_words[i].split())
            matches, clashes = self._numeric_fit(i, query)
            score = cosine + WORD_BONUS * len(shared) + NUMERIC_BONUS * matches
            score -= NUMERIC_CLASH * clashes
            fixed_matches, fixed_clashes, flags = self._fixed_fit(i, query_stems)
            score += FIXED_BONUS * fixed_matches - FIXED_CLASH * fixed_clashes - FIXED_FLAG * flags
            score -= TEMPLATE_PENALTY * self.templates[i]
            score -= NAME_EXTRA_PENALTY * len(stems(self.names[i]) - query_stems)
            score += UNIT_BONUS * (unit is not None and unit in self.units[i])
            scored.append((score, cosine, i, shared, matches))
        # Sorted by the raw sum: the cap at 1.0 must not make near-perfect matches equal
        scored.sort(key=lambda row: row[0], reverse=True)
        return [
            {
                **self.brief(i),
                "score": round(min(1.0, score), 4),
                "cosine": round(cosine, 4),
                "shared_words": sorted(shared)[:12],
                "matched_characteristics": self._matched_chars(i, query_words),
                "numeric_matches": matches,
            }
            for score, cosine, i, shared, matches in scored[:TOP_K]
        ]

    def _matched_chars(self, i: int, query_words: set[str]) -> list[str]:
        matched = []
        for name, values in self.chars[i]:
            for value in values:
                value_words = words(value)
                if value_words and value_words <= query_words:
                    matched.append(f"{name}: {value}")
                    break
        return matched[:6]

    # --- lookup ----------------------------------------------------------------------------

    def brief(self, i: int) -> dict:
        return {
            "code": self.codes[i],
            "name": self.names[i],
            "okpd2_code": self.okpd2[i],
            "status": self.statuses[i],
            "characteristics": [[name, values[:5]] for name, values in self.chars[i][:6]],
        }

    def get(self, code: str) -> dict | None:
        i = self.index.get(code.strip())
        return None if i is None else self.brief(i)

    def lookup(self, query: str, limit: int = 20) -> list[dict]:
        """Manual correction: by code prefix or by words of the name; ACTIVE codes first."""
        query = query.strip().lower().replace("ё", "е")
        if not query:
            return []
        if re.fullmatch(r"[\d.\-]+", query):
            found = [i for i, code in enumerate(self.codes) if code.startswith(query)]
        else:
            parts = query.split()
            found = [i for i, n in enumerate(self._names_lower) if all(p in n for p in parts)]
        found.sort(key=lambda i: (not self.active[i], len(self.names[i]), self.codes[i]))
        return [self.brief(i) for i in found[:limit]]


# --- loading -------------------------------------------------------------------------------


def fixed_characteristics(characteristics: list) -> list[list]:
    """[[name, [values]]] of the characteristics the customer may not change."""
    fixed = []
    for characteristic in characteristics or []:
        if (characteristic.get("kind") or "").startswith("Неизменяемая"):
            values = [v.get("text") or "" for v in characteristic.get("values") or []]
            fixed.append([characteristic.get("name") or "", values[:MAX_FIXED_VALUES]])
    return fixed


def code_units(units: list | None) -> list[str]:
    """Unit keys of a code: ["Литр; кубический дециметр"] -> ["l"]."""
    keys = {unit_key(part) for unit in units or [] for part in str(unit).split(";")}
    return sorted(key for key in keys if key)


def numeric_bounds(characteristics: list) -> list[list[float]]:
    """[[min values], [max values]] of all numeric values: "≥ 3000 и < 4000" -> 3000, 4000."""
    lower, upper = set(), set()
    for characteristic in characteristics or []:
        for value in characteristic.get("values") or []:
            if isinstance(value.get("min"), int | float):
                lower.add(float(value["min"]))
            if isinstance(value.get("max"), int | float):
                upper.add(float(value["max"]))
    return [sorted(lower), sorted(upper)]


def brief_chars(characteristics: list) -> tuple[list, list[str]]:
    """Short characteristics for the UI and all their value texts for word matching."""
    brief, texts = [], []
    for characteristic in characteristics or []:
        values = [v.get("text") for v in characteristic.get("values") or [] if v.get("text")]
        texts.extend(values)
        if len(brief) < BRIEF_CHARS:
            brief.append(
                [characteristic.get("name") or "", [v[:80] for v in values[:BRIEF_VALUES]]]
            )
    return brief, texts


def build(path: Path) -> tuple[np.ndarray, dict]:
    """Streams the dump: vectors into a growing matrix, the rest into short lists."""
    vectors = np.empty((90_000, DIM), dtype=np.float32)
    meta: dict[str, list] = {
        k: []
        for k in (
            "codes",
            "statuses",
            "names",
            "okpd2",
            "words",
            "chars",
            "bounds",
            "fixed",
            "templates",
            "units",
        )
    }
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            if not record.get("embedding_b64"):
                continue
            if n == len(vectors):
                vectors = np.concatenate([vectors, np.empty((20_000, DIM), np.float32)])
            vectors[n] = np.frombuffer(base64.b64decode(record["embedding_b64"]), dtype="<f4")
            chars, value_texts = brief_chars(record.get("characteristics"))
            code_words = list(words(" ".join([record.get("name") or "", *value_texts])))
            meta["codes"].append(record["code"])
            meta["statuses"].append(record.get("status") or "ACTIVE")
            meta["names"].append(record.get("name") or "")
            meta["okpd2"].append(record.get("okpd2_code") or "")
            meta["words"].append(" ".join(code_words[:MAX_CODE_WORDS]))
            meta["chars"].append(chars)
            meta["bounds"].append(numeric_bounds(record.get("characteristics")))
            meta["fixed"].append(fixed_characteristics(record.get("characteristics")))
            meta["templates"].append(bool(record.get("is_template")))
            meta["units"].append(code_units(record.get("units")))
            n += 1
    return vectors[:n], meta  # a view: no second 334 MB copy


def load_catalog(path: Path | None = None, cache_dir: Path | None = None) -> Catalog:
    path, cache_dir = path or CATALOG_PATH, cache_dir or CACHE_DIR
    stat = path.stat()
    key = f"v{CACHE_VERSION}-{stat.st_size}-{stat.st_mtime_ns}"
    vectors_path = cache_dir / f"catalog-{key}.npy"
    meta_path = cache_dir / f"catalog-{key}.json.gz"
    if vectors_path.exists() and meta_path.exists():
        vectors = np.load(vectors_path)
        with gzip.open(meta_path, "rt", encoding="utf-8") as file:
            meta = json.load(file)
        logger.info("catalog from cache: %s codes", len(meta["codes"]))
    else:
        vectors, meta = build(path)
        logger.info("catalog built from the dump: %s codes", len(meta["codes"]))
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            for old in cache_dir.glob("catalog-*"):
                old.unlink()
            np.save(vectors_path, vectors)
            with gzip.open(meta_path, "wt", encoding="utf-8", compresslevel=1) as file:
                json.dump(meta, file, ensure_ascii=False)
        except OSError:
            logger.warning("catalog cache is not written to %s", cache_dir, exc_info=True)
    return Catalog(
        vectors=vectors,
        codes=meta["codes"],
        statuses=meta["statuses"],
        names=meta["names"],
        okpd2=meta["okpd2"],
        code_words=meta["words"],
        chars=meta["chars"],
        bounds=meta["bounds"],
        fixed=meta["fixed"],
        templates=meta["templates"],
        units=meta["units"],
    )
