"""KTRU catalog in memory and the search of candidate codes.

Catalog metadata and 256-dimensional Yandex vectors are loaded by catalog_data.py;
characteristics.py handles quantities and unit conversion. This module ranks candidates.
Search: exact pgvector inner product (NumPy cosine offline) -> 50 nearest -> word bonus
(name + characteristic values: "А4", "80 г/м2" pick the code inside a group) -> top-5.
Exact, not HNSW: on this catalog HNSW loses up to 14 % of the right codes.
"""

import functools
import gzip
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from catalog_data import catalog_vectors, load_data
from characteristics import (
    _WORD,
    BRIEF_CHARS,
    BRIEF_VALUES,
    MAX_FIXED_VALUES,
    brief_chars,
    code_units,
    fixed_characteristics,
    in_base,
    inside,
    numeric_bounds,
    query_bounds,
    same_quantity,
    unit_key,
    unit_of,
    with_unit,
)

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(os.getenv("CATALOG_PATH", "/catalog/ktru_catalog.jsonl.gz"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/cache"))
# Yandex embeddings of the codes of the dump: an .npz with "codes" and "vectors" built by
# scripts/embed_catalog.py. The old vectors of the dump (embedding_b64) are not read.
VECTORS_PATH = os.getenv("CATALOG_VECTORS", "/catalog/ktru_vectors_yandex.npz")
VECTOR_LIMIT = 50
WORD_BONUS = 0.02
TOP_K = 5
QUERY_CHUNK = 128  # 128 x 81 509 float32 = 42 MB of similarities at a time
WORDS_ONLY_BASE = 0.6  # without vectors the score stays low: nothing goes to auto
MAX_CODE_WORDS = 400
# Characteristics kept per code: candidates show 6 x 5 of them, a lookup of one code all
CACHE_VERSION = 10  # bump when the cached metadata changes
# Neighbour codes of one group often differ only by a range: "> 1.25" vs "<= 1.25".
# A bound of the row equal to a bound of the code adds NUMERIC_BONUS, so does a plain value with
# a unit inside a range of the code («Вес: 100 г» and «≥ 90 и < 110 Грамм»); the same number on
# the other side ("не менее 1.25" against "<= 1.25") takes NUMERIC_CLASH off. Numbers are in
# base units (unit_of).
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
# In an EIS form the customer fills every characteristic of the chosen code and adds the
# missing ones as «дополнительная характеристика». So a code with characteristics the row never
# names is likely a neighbour: «Мыло туалетное твердое» -02 (марка, отдушка, антибактериальный
# компонент) against -07 (the same + «Масса нетто») for a row with «Вес» as an extra.
# Counted only when the row names at least one characteristic of the code: free text
# («коровье, пастеризованное, 2,5 процента») names none and is not touched.
# A/B on stored attributes of two runs (with bare numbers out of WORD_BONUS): EIS form 1 -> 3
# of 3, 292 rows 287 -> 289, 1000 rows 953 -> 954 and 944 -> 953, real file 12/12 as before;
# 0.03 lowers the 1000 rows to 942-943.
UNNAMED_PENALTY = 0.02
# Hybrid retrieval: the top codes by BM25 over the stems of code names join the vector top-50,
# so a code whose name matches the row is ranked even when its vector was not among the 50.
# They only join the candidates: a BM25 bonus in the score (0.01-0.1) lowered the exact code on
# the draft reference by 0.3-4.5 points. 0 turns the lexical part off.
LEX_LIMIT = int(os.getenv("LEX_LIMIT", "20"))
BM25_K1, BM25_B = 1.2, 0.75
# Twin codes differ only by the unit: «Кефир» in litres and in kilograms. The unit of the row
# (the «Ед. изм.» column, else the unit the product is usually bought in) decides such a tie;
# A unit written in the file by the customer is worth more than a near-tie of the cosine; the
# unit the network guessed (dairy in litres) settles a tie. 0.005 -> 0.01 on 28.10.2026 together
# with the synonyms below, replayed on two runs of saved LLM answers: auto rows 845 -> 887 and
# 828 -> 870 on the 1000-row file and its copy, wrong codes in auto 7 -> 7 on every set, no row
# broken; 0.02 put a wrong dairy code of the real file into auto.
UNIT_BONUS = 0.01
COLUMN_UNIT_BONUS = 0.03

# Lowercased unit -> key; full names by prefix (longer first), short forms exactly

# Latin letters that look like Cyrillic ones: "A4" and "А4" must be the same word
_LOOKALIKE = str.maketrans("aceopxkmhbty", "асеорхкмнвту")
# A decimal number is one word: «2,5 процента» must not match the values «2» and «≥ 2»
# «Нужно: ноутбук Lenovo…» - the name follows the request word (human_1000 has 132 such rows)
REQUEST_PREFIX = re.compile(r"^\s*(нужн[оаы]?|требу[ею]тся|необходим[оаы]?)\s*:\s*", re.IGNORECASE)
_DECIMAL = re.compile(r"\d+(?:\.\d+)+")

# Numbers are compared in one unit per quantity: «2 м» = «2000 Миллиметр», «0,1 кг» = «100 г».
# The base is the most common unit of the quantity in the catalog (Миллиметр: 150 392 values,
# Килограмм, Кубический сантиметр, Гигабайт), so a number without a unit still meets most codes
# in their own unit. Unit -> (factor, quantity); full names by prefix (longer first), short forms
# as a whole word. «Кубический метр в час», «г/м2», «Грамм на квадратный метр» are other
# quantities: a unit followed by «в», «на» or «/» is not converted.
# The quantity keeps numbers of different things apart: «16 ГБ» of memory is not inside
# «≥ 14 Штука» of processor cores, «15.6 Дюйм» is not inside a range of millimetres.


# Names of characteristics that customers write otherwise than the catalog: «жирность 2,5 %»
# is «Массовая доля жира», so the value is compared with that characteristic (milk of the real
# file got its code back). Only the ranking sees them, the embedding gets the row as written.
SYNONYMS = (
    (re.compile(r"\bжирност[а-яё]*", re.IGNORECASE), "массовая доля жира"),
    (re.compile(r"\bвес\b", re.IGNORECASE), "масса"),
)


def with_synonyms(text: str) -> str:
    for pattern, name in SYNONYMS:
        text = pattern.sub(name, text)
    return text


def words(text: str) -> set[str]:
    """Words for matching: 3+ letters or with a digit, lowercase, ё=е, Latin lookalikes."""
    text = (text or "").lower().replace("ё", "е").translate(_LOOKALIKE)
    found = (w.replace(",", ".") for w in _WORD.findall(text))
    return {w for w in found if len(w) >= 3 or any(c.isdigit() for c in w)}


def is_number(word: str) -> bool:
    return word.isdigit() or _DECIMAL.fullmatch(word) is not None


def name_key(name: str) -> tuple[str, ...]:
    """The words of a lowercase name in any order."""
    return tuple(sorted(_WORD.findall(name)))


def same_product(name: str, catalog_name: str) -> bool:
    """The best code is the product the row names: the same words in any order («Поручень
    опорный», «Опорный поручень») or a more specific name that starts with them («Анализатор
    биохимический» -> «… метаболического профиля»). «3D-сканер стационарный» has the word
    «сканер» but is another product than «Сканер»."""
    asked = _WORD.findall(name.lower().replace("ё", "е"))
    best = _WORD.findall(catalog_name.lower().replace("ё", "е"))
    return set(asked) == set(best) or best[: len(asked)] == asked


def stems(text: str) -> set[str]:
    """First 5 letters of the words: «коровье» and «коровьего» are the same. A decimal number
    stays whole: «12.75» is not «12.7»."""
    return {w if _DECIMAL.fullmatch(w) else w[:5] for w in words(text)}


# Names and values of the catalog come up for every row again: their words are kept, not
# recomputed (a third of the search time on 1000 rows went to the same few thousand strings)
CATALOG_TEXT_CACHE = 100_000


@functools.lru_cache(maxsize=CATALOG_TEXT_CACHE)
def catalog_stems(text: str) -> frozenset[str]:
    return frozenset(stems(text))


@functools.lru_cache(maxsize=CATALOG_TEXT_CACHE)
def catalog_words(text: str) -> frozenset[str]:
    return frozenset(words(text))


def is_named(name: str, query_stems: set[str]) -> bool:
    """The row has at least half of the words of a characteristic name."""
    name_stems = catalog_stems(name)
    return len(name_stems & query_stems) >= max(1, len(name_stems) // 2)


def written_value(text: str, name: str) -> set[str] | None:
    """Stems written right after «name:» in the row, up to the next colon; None if absent.

    «Объем установленной памяти: ≥ 48 ГБ ... объем увеличения памяти: 16 ГБ» - the value of
    the first characteristic is 48, not 16, although both numbers are in the row.
    text and name are lowercase with ё = е."""
    at = text.find(name)
    while at >= 0:
        rest = text[at + len(name) :].lstrip()
        starts_word = at == 0 or not text[at - 1].isalpha()
        if starts_word and rest.startswith(":"):
            rest = rest[1:]
            end = rest.find(":")
            return stems(rest if end < 0 else rest[:end])
        at = text.find(name, at + 1)
    return None


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
        # lowercase name -> its codes: a name written in the row is found at once
        self._name_groups: dict[str, list[int]] = {}
        # the same words in another order: «Цифровой микроскоп» is the catalog's «Микроскоп
        # цифровой», «Видеокамера экшн» its «Экшн видеокамера»
        self._name_orders: dict[tuple[str, ...], list[int]] = {}
        for i, name in enumerate(self._names_lower):
            self._name_groups.setdefault(name, []).append(i)
            key = name_key(name)
            if len(key) > 1:
                self._name_orders.setdefault(key, []).append(i)
        self._postings: dict[str, np.ndarray] | None = None
        self._bm25: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
        self._lock = threading.Lock()

    # --- search ----------------------------------------------------------------------------

    def search(
        self,
        vectors: np.ndarray | None,
        texts: list[str],
        include_inactive: bool = False,
        units: list[str | None] | None = None,
        column_units: list[str | None] | None = None,
        anchors: list[str] | None = None,
    ) -> list[list[dict]]:
        """Top-5 candidates for every text; vectors=None -> by words only.

        anchors: the rows as the customer wrote them; the product name at their start is
        kept among the candidates (see _anchored)."""
        texts = [with_synonyms(text) for text in texts]
        query_words = [words(text) for text in texts]
        if vectors is not None:
            nearest = self._nearest_by_vectors(vectors, include_inactive)
        else:
            nearest = [self._nearest_by_words(qw, include_inactive) for qw in query_words]
        if vectors is not None and LEX_LIMIT > 0:
            nearest = [
                self._merge(near, self._lexical(stems(text), include_inactive), vector)
                for near, text, vector in zip(nearest, texts, vectors, strict=True)
            ]
        units = units or [None] * len(texts)
        column_units = column_units or [None] * len(texts)
        ranked = [
            self._rank(qw, query_bounds(text), stems(text), near, unit, text, column_unit)
            for qw, text, near, unit, column_unit in zip(
                query_words, texts, nearest, units, column_units, strict=True
            )
        ]
        if anchors is None:
            return [[c | {"words_only": vectors is None} for c in found] for found in ranked]
        rows = zip(
            anchors,
            texts,
            ranked,
            vectors if vectors is not None else [None] * len(texts),
            units,
            column_units,
            strict=True,
        )
        return [
            [c | {"words_only": vectors is None} for c in self._anchored(*row, include_inactive)]
            for row in rows
        ]

    def _anchored(self, anchor, text, found, vector, unit, column_unit, include_inactive):
        """Keeps the product the row names when the ranking lost it: «клавиатура USB чёрная»
        went to tea and «мышь с проводом 1,5 м» to fuel oil, characteristics outweighed the name.

        If the start of the row is a catalog name and the best candidate lacks its words, the
        codes of that name are ranked instead, with real cosines and the same rules; a more
        specific name that has all the words stays. The name comes from the row, not from the
        search hint of the LLM: «Изделия сухарные» must not become «Сухари». Measurements:
        docs/final-report-2026-10-28.md."""
        anchor = REQUEST_PREFIX.sub("", anchor)
        if vector is None:
            return self._anchored_word(
                anchor, text, found, vector, unit, column_unit, include_inactive
            )
        head = re.split(r"\.\s|[,;:]", anchor, maxsplit=1)[0].lower().replace("ё", "е").split()
        for end in range(len(head), 0, -1):
            name = " ".join(head[:end])
            codes = self._name_groups.get(name) or self._name_orders.get(name_key(name), [])
            pool = [i for i in codes if include_inactive or self.active[i]]
            if not pool:
                continue
            # compared with the whole name of the row too: «Поручень опорный» is not a catalog
            # name, «Поручень» is, and the best «Опорный поручень» is the row's product
            whole = " ".join(head)
            if found and (
                same_product(name, found[0]["name"]) or same_product(whole, found[0]["name"])
            ):
                return found
            return self._rank_pool(pool, text, vector, unit, column_unit)
        # A synonym can keep the qualifier: «стул офисный» found «кресло офисное»
        qualifiers = {w[:5] for w in words(" ".join(head[1:])) if re.search(r"[а-я]", w)}
        if found and qualifiers & stems(found[0]["name"]):
            return found
        return self._anchored_word(anchor, text, found, vector, unit, column_unit, include_inactive)

    def _anchored_word(self, anchor, text, found, vector, unit, column_unit, include_inactive):
        """The first word of the row (3+ letters) must be in the best name; if it is not, the
        codes whose name has that word are ranked instead."""
        first = re.search(r"[а-яА-ЯёЁa-zA-Z]{3,}", anchor)
        first_words = words(first.group()) if first else set()
        if not first_words or (found and first_words & words(found[0]["name"])):
            return found
        postings = self._name_postings()
        pool = {int(i) for word in first_words for i in postings.get(word, [])}
        pool = sorted(i for i in pool if include_inactive or self.active[i])
        if not pool:
            return found
        return self._rank_pool(pool, text, vector, unit, column_unit)

    def _rank_pool(self, pool, text, vector, unit, column_unit) -> list[dict]:
        qw = words(text)
        if vector is None:
            nearest = [
                (i, WORDS_ONLY_BASE * len(qw & words(self.names[i])) / max(1, len(qw)))
                for i in pool
            ]
        else:
            cosines = self.vectors[pool] @ vector
            nearest = [(i, float(c)) for i, c in zip(pool, cosines, strict=True)]
        return self._rank(qw, query_bounds(text), stems(text), nearest, unit, text, column_unit)

    def _bm25_postings(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """stem -> (code indices, BM25 weight of the stem in that code name)."""
        with self._lock:
            if self._bm25 is None:
                docs = [stems(name) for name in self.names]
                lengths = np.array([max(1, len(d)) for d in docs], dtype=np.float32)
                norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths / lengths.mean())
                lists: dict[str, list[int]] = {}
                for i, doc in enumerate(docs):
                    for stem in doc:
                        lists.setdefault(stem, []).append(i)
                n = len(docs)
                self._bm25 = {}
                for stem, ix in lists.items():
                    idx = np.array(ix, dtype=np.int32)
                    idf = np.log(1 + (n - len(ix) + 0.5) / (len(ix) + 0.5))
                    self._bm25[stem] = (idx, (idf * (BM25_K1 + 1) / (1 + norm[idx])))
            return self._bm25

    def _bm25_scores(self, query_stems: set[str]) -> np.ndarray:
        """BM25 of every code name for the stems; stems that no name has add nothing."""
        postings = self._bm25_postings()
        scores = np.zeros(len(self.codes), dtype=np.float32)
        for stem in query_stems:
            if stem in postings:
                idx, weight = postings[stem]
                scores[idx] += weight
        return scores

    def _lexical(self, query_stems: set[str], include_inactive: bool) -> list[int]:
        """Indices of the top codes by BM25 of the name."""
        scores = self._bm25_scores(query_stems)
        if not include_inactive:
            scores[~self.active] = 0
        limit = min(LEX_LIMIT, len(self.codes))
        top = np.argpartition(-scores, limit - 1)[:limit]
        return [int(i) for i in top if scores[i] > 0]

    def _merge(self, nearest: list, lexical: list[int], vector: np.ndarray) -> list:
        """Vector top + lexical top; lexical-only codes get their real cosine."""
        seen = {i for i, _ in nearest}
        extra = [i for i in lexical if i not in seen]
        if not extra:
            return nearest
        cosines = self.vectors[extra] @ vector
        return nearest + [(i, float(c)) for i, c in zip(extra, cosines, strict=True)]

    def _nearest_by_vectors(self, queries: np.ndarray, include_inactive: bool) -> list[list]:
        limit = min(VECTOR_LIMIT, len(self.codes))
        if getattr(self, "vector_store", None) is not None:
            return self.vector_store.nearest(queries, include_inactive, limit)
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

    def _numeric_fit(self, i: int, query: tuple[set, set, set]) -> tuple[int, int]:
        """(bounds of the row the code has + plain values inside a range of the code,
        bounds the code has only on the other side). Only numbers of one quantity meet."""
        lower, upper, plain = query
        ranges = self.bounds[i]

        def has(value: float, quantity: str | None, side: int) -> bool:
            return any(r[side] == value and same_quantity(quantity, r[2]) for r in ranges)

        matches = sum(has(v, q, 0) for v, q in lower) + sum(has(v, q, 1) for v, q in upper)
        matches += sum(any(q == r[2] and inside(v, r) for r in ranges) for v, q in plain)
        clashes = sum(has(v, q, 1) and not has(v, q, 0) for v, q in lower)
        clashes += sum(has(v, q, 0) and not has(v, q, 1) for v, q in upper)
        return matches, clashes

    def _fixed_fit(self, i: int, query_stems: set[str], text: str = "") -> tuple[float, int, int]:
        """(fixed values written in the row, fixed values the row contradicts, unasked «Да»).

        When the row has «name: value» for the characteristic, the value is compared there;
        otherwise anywhere in the row. A match counts 1 / (allowed values): a code allowing only
        «≥ 2» says more than a code allowing «≥ 1, ≥ 2, ... ≥ 16». text: lowercase, ё = е."""
        matches = clashes = flags = 0
        for name, values in self.fixed[i]:
            named = is_named(name, query_stems)
            if len(values) == 1 and values[0].lower() in ("да", "нет"):
                flags += values[0].lower() == "да" and not named
                continue
            written = written_value(text, name.lower().replace("ё", "е")) if text else None
            where = query_stems if written is None else written
            if any(catalog_stems(value) and catalog_stems(value) <= where for value in values):
                matches += 1 / len(values)
            elif named:
                clashes += 1
        return matches, clashes, flags

    def _unnamed(self, i: int, query_stems: set[str]) -> int:
        """Characteristics of the code the row does not name; 0 if it names none of them."""
        named = [is_named(name, query_stems) for name, _, _ in self.chars[i]]
        return named.count(False) if any(named) else 0

    def _rank(
        self,
        query_words: set[str],
        query: tuple[set, set, set],
        query_stems: set[str],
        nearest: list,
        unit: str | None = None,
        text: str = "",
        column_unit: str | None = None,
    ) -> list[dict]:
        scored = []
        low = text.lower().replace("ё", "е")
        for i, cosine in nearest:
            # Bare numbers are compared as bounds below: as words «100 Грамм» matched the mass
            # range «≥ 100 и < 110» of a neighbour code and outweighed the right one
            shared = {w for w in query_words & set(self.code_words[i].split()) if not is_number(w)}
            matches, clashes = self._numeric_fit(i, query)
            score = cosine + WORD_BONUS * len(shared) + NUMERIC_BONUS * matches
            score -= NUMERIC_CLASH * clashes
            fixed_matches, fixed_clashes, flags = self._fixed_fit(i, query_stems, low)
            score += FIXED_BONUS * fixed_matches - FIXED_CLASH * fixed_clashes - FIXED_FLAG * flags
            score -= TEMPLATE_PENALTY * self.templates[i]
            score -= NAME_EXTRA_PENALTY * len(catalog_stems(self.names[i]) - query_stems)
            score -= UNNAMED_PENALTY * self._unnamed(i, query_stems)
            score += UNIT_BONUS * (unit is not None and unit in self.units[i])
            score += COLUMN_UNIT_BONUS * (column_unit is not None and column_unit in self.units[i])
            scored.append((score, cosine, i, shared, matches))
        # Sorted by the raw sum: the cap at 1.0 must not make near-perfect matches equal;
        # the confidence takes the gap between the first two from raw_score for the same reason
        scored.sort(key=lambda row: row[0], reverse=True)
        return [
            {
                **self.brief(i),
                "score": round(min(1.0, score), 4),
                "raw_score": round(score, 4),
                "cosine": round(cosine, 4),
                "shared_words": self._display_words(i, shared),
                "matched_characteristics": self._matched_chars(i, query_words),
                "numeric_matches": matches,
            }
            for score, cosine, i, shared, matches in scored[:TOP_K]
        ]

    def _display_words(self, i: int, shared: set[str]) -> list[str]:
        wanted = sorted(shared)[:12]
        shown = {}
        texts = [self.names[i]]
        for name, values, _ in self.chars[i]:
            texts.append(name)
            texts.extend(values)
        for text in texts:
            for token in re.findall(r"\w+(?:[.,]\w+)*", text):
                for key in words(token) & shared:
                    shown.setdefault(key, token if re.search(r"[A-Za-z]", token) else token.lower())
        return [shown.get(word, word) for word in wanted]

    def _matched_chars(self, i: int, query_words: set[str]) -> list[str]:
        matched = []
        for name, values, _ in self.chars[i]:
            for value in values:
                value_words = catalog_words(value)
                if value_words and value_words <= query_words:
                    matched.append(f"{name}: {value}")
                    break
        return matched[:6]

    # --- lookup ----------------------------------------------------------------------------

    def brief(self, i: int, full: bool = False) -> dict:
        """A code for the UI; full: every kept characteristic and the unit keys of the code."""
        chars = self.chars[i] if full else [[n, v[:5], u] for n, v, u in self.chars[i][:6]]
        found = {
            "code": self.codes[i],
            "name": self.names[i],
            "okpd2_code": self.okpd2[i],
            "status": self.statuses[i],
            "characteristics": [[n, [with_unit(v, u) for v in values]] for n, values, u in chars],
        }
        return found | {"units": self.units[i]} if full else found

    def get(self, code: str) -> dict | None:
        i = self.index.get(code.strip())
        return None if i is None else self.brief(i)

    def lookup(self, query: str, limit: int = 20, include_inactive: bool = True) -> list[dict]:
        """Manual correction: by code prefix or by words of the name; ACTIVE codes first.

        Names that have every word of the query come first; when there are none, the query is
        matched by word stems (case endings, a stray «А4» or «2,5%» do not empty the answer).
        A word of the query starts a word of the name: «стул» does not find «фистульный».
        Without include_inactive the inactive codes are dropped before the limit, so a broad
        query still gives up to limit active codes."""
        query = query.strip().lower().replace("ё", "е")
        if not query:
            return []
        is_code = re.fullmatch(r"[\d.\-]+", query) is not None
        if is_code:
            found = [i for i, code in enumerate(self.codes) if code.startswith(query)]
        else:
            parts = query.split()
            patterns = [re.compile(r"\b" + re.escape(part)) for part in parts]
            found = [
                i for i, n in enumerate(self._names_lower) if all(p.search(n) for p in patterns)
            ]
        if not include_inactive:
            found = [i for i in found if self.active[i]]
        if not found and not is_code:
            return [self.brief(i) for i in self._by_stems(query, limit, include_inactive)]
        found.sort(key=lambda i: (not self.active[i], len(self.names[i]), self.codes[i]))
        return [self.brief(i, full=self.codes[i] == query) for i in found[:limit]]

    def _by_stems(self, query: str, limit: int, include_inactive: bool = True) -> list[int]:
        """Codes whose name shares word stems with the query: ACTIVE first, then best BM25."""
        scores = self._bm25_scores(stems(query))
        if not include_inactive:
            scores[~self.active] = 0
        hits = np.flatnonzero(scores > 0)
        order = np.lexsort((-scores[hits], ~self.active[hits]))
        return [int(i) for i in hits[order][:limit]]


# --- loading -------------------------------------------------------------------------------


def build(path: Path) -> dict:
    """Streams the dump into short lists; the vectors come from VECTORS_PATH."""
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
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            # the vectors file has the codes that had a vector in the dump (embed_catalog.py)
            if not record.get("embedding_b64"):
                continue
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
    return meta


def load_catalog(
    path: Path | None = None, cache_dir: Path | None = None, vectors_file: str | None = None
) -> Catalog:
    """Load validated catalog data and construct the in-memory search index."""
    vectors, meta = load_data(
        path or CATALOG_PATH,
        cache_dir or CACHE_DIR,
        VECTORS_PATH if vectors_file is None else vectors_file,
        build,
        CACHE_VERSION,
    )
    return Catalog(
        vectors=vectors,
        code_words=meta["words"],
        **{key: value for key, value in meta.items() if key != "words"},
    )


__all__ = [
    "BRIEF_CHARS",
    "BRIEF_VALUES",
    "MAX_FIXED_VALUES",
    "Catalog",
    "brief_chars",
    "catalog_vectors",
    "code_units",
    "fixed_characteristics",
    "in_base",
    "inside",
    "load_catalog",
    "numeric_bounds",
    "query_bounds",
    "same_quantity",
    "unit_key",
    "unit_of",
    "with_unit",
]
