# -*- coding: utf-8 -*-
"""CSV 수용 계층 — 바이트에서 저장 가능한 표까지.

바깥에서 들어온 CSV 는 무엇이든 깨질 수 있다고 본다. 이 모듈은
인코딩·구분자를 스스로 알아내고, 숫자와 시각을 되살리고, 되살릴 수
없는 것은 '조용히 버리지 않고' 보고한다.

Flask 를 모른다 — 순수 함수라 따로 시험할 수 있다.
손잡이는 전부 config/app.yaml 의 `csv:` 가 단일 소스다.
"""
from __future__ import annotations

import csv as _csv
import io
import re
import math
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from .app_config import cfg


# ── 오류 ──────────────────────────────────────────────────────
class CsvIngestError(Exception):
    """사용자가 고칠 수 있는 입력 문제. 400 으로 내보낸다."""

    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


# ── 보고서 ────────────────────────────────────────────────────
@dataclass
class IngestReport:
    """무엇을 어떻게 읽었는지. 그대로 데이터셋 메타로 들어간다."""

    encoding: str = ""
    encoding_source: str = "detected"      # detected | bom | user
    delimiter: str = ","
    delimiter_source: str = "detected"     # detected | user
    rows_read: int = 0
    rows_stored: int = 0
    rows_dropped_bad_time: int = 0
    duplicates_found: int = 0
    duplicate_policy: str = ""
    duplicate_examples: list = field(default_factory=list)
    numeric_recovered: list = field(default_factory=list)
    values_coerced: int = 0
    time_col: str = ""
    index_type: str = ""
    time_format: str = ""
    index_offset: int = 0
    time_start: str = ""
    time_end: str = ""
    sampling_interval: str = ""
    warnings: list = field(default_factory=list)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["delimiter"] = _readable_sep(d["delimiter"])
        return d


def _readable_sep(sep: str) -> str:
    return {"\t": "\\t"}.get(sep, sep)


# ── 1. 인코딩 ─────────────────────────────────────────────────
_BOMS = [
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
]


def _text_score(text: str) -> float:
    """읽을 만한 글자의 비율. 모지바케는 제어문자·희귀기호가 튀어 점수가 낮다."""
    sample = text[:20000]
    if not sample:
        return 0.0
    ok = 0
    for ch in sample:
        if ch in "\r\n\t":
            ok += 1
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C"):          # 제어·미배정
            continue
        if cat in ("So", "Co", "Cn"):    # 기타기호·사용자영역
            continue
        ok += 1
    return ok / len(sample)


def _reject_binary(text: str) -> None:
    """NUL 이 섞여 있으면 표가 아니다. 구분자 추측까지 가면 엉뚱한 소리를 한다."""
    if "\x00" in text[:20000]:
        raise CsvIngestError("글자 파일이 아닙니다(이진 파일로 보입니다). "
                             "CSV 로 저장해서 올리세요.")


def decode_bytes(raw: bytes, encoding: str | None = None) -> tuple[str, str, str]:
    """(본문, 쓴 인코딩, 출처). 실패는 예외로 드러낸다 — 몰래 치환하지 않는다."""
    if encoding:
        try:
            return raw.decode(encoding), encoding, "user"
        except (UnicodeDecodeError, LookupError) as e:
            raise CsvIngestError(
                f"'{encoding}' 로 읽지 못했습니다. 인코딩을 다시 고르세요.",
                {"encoding": encoding, "reason": str(e)},
            ) from e

    for bom, enc in _BOMS:
        if raw.startswith(bom):
            try:
                return raw.decode(enc), enc, "bom"
            except UnicodeDecodeError:
                break

    candidates = cfg("csv.encoding_candidates")
    min_score = cfg("csv.encoding_min_score")
    best = None
    for enc in candidates:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        score = _text_score(text)
        if score >= min_score:
            return text, enc, "detected"
        if best is None or score > best[2]:
            best = (text, enc, score)

    if best is None:
        raise CsvIngestError(
            "글자 인코딩을 알아내지 못했습니다. 업로드 화면에서 직접 고르세요.",
            {"tried": candidates},
        )
    return best[0], best[1], "detected"


# ── 2. 구분자 ─────────────────────────────────────────────────
def sniff_delimiter(text: str, delimiter: str | None = None) -> tuple[str, str]:
    """후보별로 실제 파싱해 본다. 열 수가 일정하면서 가장 많이 쪼개지는 것이 정답."""
    if delimiter:
        return delimiter, "user"

    candidates = cfg("csv.delimiter_candidates")
    n_lines = cfg("csv.delimiter_sample_lines")
    sample = "\n".join(text.splitlines()[:n_lines])
    if not sample.strip():
        raise CsvIngestError("빈 파일입니다.")

    best = (",", 1, False)
    for sep in candidates:
        try:
            rows = list(_csv.reader(io.StringIO(sample), delimiter=sep))
        except _csv.Error:
            continue
        rows = [r for r in rows if r]
        if len(rows) < 2:
            continue
        widths = [len(r) for r in rows]
        consistent = len(set(widths)) == 1
        n = widths[0]
        if n < 2:
            continue
        # 일정한 쪽이 먼저, 그 다음 많이 쪼개지는 쪽
        if (consistent, n) > (best[2], best[1]):
            best = (sep, n, consistent)

    # 값만 한 줄씩 적힌 파일도 흔하다. 나눌 것이 없다고 거절하지 않는다.
    if best[1] < 2:
        return ",", "single_column"
    return best[0], "detected"


# ── 3. 표 읽기 ────────────────────────────────────────────────
# 천단위는 반드시 세 자리씩 묶인다: 1,234 / 12,345,678 / 1,234.56
_THOUSANDS_RE = re.compile(r"^\s*[-+]?\d{1,3}(,\d{3})+(\.\d+)?\s*$")
# 소수점 쉼표: 12,5 / 0,345 -- 쉼표 뒤 자릿수가 3이 아니면 천단위일 수 없다
_DEC_COMMA_RE = re.compile(r"^\s*[-+]?\d+,\d+\s*$")


def _comma_kind(sample: pd.Series, delimiter: str) -> str:
    """쉼표가 낀 열의 정체를 가른다: 'thousands' | 'decimal' | ''.

    0,5 를 천단위로 잘못 읽으면 값이 10배가 된다. 확실할 때만 손댄다.
    """
    vals = [v for v in sample if "," in v]
    if not vals:
        return ""

    thousands_all = all(_THOUSANDS_RE.match(v) for v in vals)
    decimal_all = all(_DEC_COMMA_RE.match(v) for v in vals)
    # 쉼표 뒤 자릿수가 3이 아닌 값이 하나라도 있으면 천단위가 아니다
    non_triple = any(len(v.rsplit(",", 1)[1].split(".")[0].strip()) != 3 for v in vals
                     if _DEC_COMMA_RE.match(v))

    mode = cfg("csv.decimal_comma")
    if mode is True and decimal_all:
        return "decimal"
    if mode is False:
        return "thousands" if thousands_all else ""

    # auto
    if decimal_all and non_triple:
        return "decimal"
    if thousands_all:
        # 1,234 처럼 세 자리 묶음뿐이면 갈리지 않는다. 구분자가 쉼표가 아닌
        # 파일(유럽식 ;)에서는 소수점일 가능성이 높으므로 손대지 않는다.
        if delimiter != "," and decimal_all:
            return ""
        return "thousands"
    return ""


def _recover_numeric(df: pd.DataFrame, report: IngestReport,
                     delimiter: str = ",") -> pd.DataFrame:
    """쉼표가 낀 숫자 열을 되살린다. 천단위와 소수점 쉼표를 구분한다."""
    if not cfg("csv.recover_thousands"):
        return df
    ratio_need = cfg("csv.numeric_recover_ratio")
    decimals = []

    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        sample = s.astype(str).head(200)
        if not sample.str.contains(",").any():
            continue

        kind = _comma_kind(sample, delimiter)
        if not kind:
            continue

        # 쉼표를 뺀(또는 점으로 바꾼) 뒤 숫자가 되는 비율을 본다
        text = df[col].astype(str).str.strip()
        if kind == "thousands":
            text = text.str.replace(",", "", regex=False)
        else:
            text = text.str.replace(",", ".", regex=False)
        converted = pd.to_numeric(text, errors="coerce")
        if converted.notna().sum() / max(df[col].notna().sum(), 1) < ratio_need:
            continue

        df[col] = converted
        (report.numeric_recovered if kind == "thousands" else decimals).append(col)

    if report.numeric_recovered:
        report.warn(f"천단위 쉼표를 숫자로 되살린 열: {', '.join(report.numeric_recovered)}")
    if decimals:
        report.numeric_recovered.extend(decimals)
        report.warn(f"쉼표를 소수점으로 읽은 열: {', '.join(decimals)} "
                    f"(config/app.yaml 의 csv.decimal_comma)")
    return df


def read_table(raw: bytes,
               encoding: str | None = None,
               delimiter: str | None = None) -> tuple[pd.DataFrame, IngestReport]:
    """바이트 → DataFrame. 인코딩·구분자·결측표기·천단위쉼표를 모두 여기서 흡수한다."""
    report = IngestReport()
    text, enc, enc_src = decode_bytes(raw, encoding)
    _reject_binary(text)
    report.encoding, report.encoding_source = enc, enc_src

    sep, sep_src = sniff_delimiter(text, delimiter)
    report.delimiter, report.delimiter_source = sep, sep_src

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=sep,
            na_values=cfg("csv.na_values"),
            keep_default_na=True,
            skip_blank_lines=True,
        )
    except Exception as e:
        raise CsvIngestError(f"표를 읽지 못했습니다: {e}",
                             {"encoding": enc, "delimiter": _readable_sep(sep)}) from e

    if df.empty:
        raise CsvIngestError("내용이 없는 표입니다.")

    df.columns = [str(c).strip() for c in df.columns]
    if df.columns.duplicated().any():
        raise CsvIngestError("공백을 제거한 뒤 열 이름이 중복됩니다. 열 이름을 구분해 주세요.")

    # pandas 가 겹친 이름에 .1 을 붙여 넘겨준다. 원본이 겹쳤다는 사실을 남긴다.
    renamed = [c for c in df.columns if re.fullmatch(r".+\.\d+", str(c))]
    if renamed:
        report.warn(f"열 이름이 겹쳐 번호를 붙였습니다: {', '.join(renamed)}")

    # 값만 있는 파일은 행 번호를 인덱스로 세워야 시계열로 쓸 수 있다
    if len(df.columns) == 1:
        df.insert(0, "row_index", range(len(df)))
        report.warn("열이 하나뿐이라 행 번호를 인덱스로 붙였습니다(row_index).")

    unnamed = [c for c in df.columns if str(c).startswith("Unnamed:")]
    empty_unnamed = [c for c in unnamed if df[c].isna().all()]
    if empty_unnamed:
        df = df.drop(columns=empty_unnamed)
        report.warn(f"줄 끝 구분자로 생긴 빈 열 {len(empty_unnamed)}개를 버렸습니다.")
    kept = [c for c in unnamed if c not in empty_unnamed]
    if kept:
        report.warn(f"이름 없는 열 {len(kept)}개를 그대로 두었습니다.")

    df = _recover_numeric(df, report, sep)
    report.rows_read = len(df)
    return df, report


# ── 4. 시각 열 정규화 ─────────────────────────────────────────
def normalize_time(df: pd.DataFrame, time_col: str, index_type: str,
                   report: IngestReport) -> pd.DataFrame:
    """시각을 ISO 문자열로 고정한다. 문자열 정렬 = 시간 정렬이 되게 하는 것이 목적."""
    report.time_col, report.index_type = time_col, index_type

    if index_type == "datetime":
        parsed = pd.to_datetime(df[time_col], errors="coerce",
                                dayfirst=cfg("csv.time.dayfirst"))
        if not pd.api.types.is_datetime64_any_dtype(parsed):
            raise CsvIngestError("시간대가 섞인 시각은 지원하지 않습니다. 같은 시간대로 변환해 주세요.")
        bad = int(parsed.isna().sum())
        ratio = bad / max(len(parsed), 1)
        if ratio > cfg("csv.time.max_unparsed_ratio"):
            examples = df.loc[parsed.isna(), time_col].astype(str).head(5).tolist()
            raise CsvIngestError(
                f"'{time_col}' 의 {ratio:.0%} 를 시각으로 읽지 못했습니다. "
                f"다른 열을 고르거나 형식을 확인하세요.",
                {"column": time_col, "unparsed": bad, "examples": examples},
            )
        if bad:
            report.rows_dropped_bad_time = bad
            report.warn(f"시각을 읽지 못한 {bad}행을 제외했습니다.")
            df = df.loc[parsed.notna()].copy()
            parsed = parsed.loc[parsed.notna()]

        if getattr(parsed.dt, "tz", None) is not None:
            report.warn("시간대 정보를 떼고 현지 시각으로 저장했습니다.")
            parsed = parsed.dt.tz_localize(None)

        midnight_only = bool((parsed.dt.normalize() == parsed).all())
        fmt = cfg("csv.time.date_format") if midnight_only else cfg("csv.time.datetime_format")
        report.time_format = fmt
        df[time_col] = parsed.dt.strftime(fmt)

        if len(parsed):
            report.time_start = parsed.min().strftime(fmt)
            report.time_end = parsed.max().strftime(fmt)
            gaps = parsed.sort_values().diff().dropna()
            gaps = gaps[gaps > pd.Timedelta(0)]
            if len(gaps):
                report.sampling_interval = str(gaps.mode().iloc[0])
        if _ambiguous_dayfirst(df, time_col):
            order = "일-월" if cfg("csv.time.dayfirst") else "월-일"
            report.warn(f"날짜 자리 해석이 모호해 {order} 순으로 읽었습니다 "
                        f"(config/app.yaml 의 csv.time.dayfirst).")

    elif index_type == "numeric":
        nums = pd.to_numeric(df[time_col], errors="coerce")
        if nums.isin([float("inf"), float("-inf")]).any():
            raise CsvIngestError("숫자 인덱스에 무한대 값을 사용할 수 없습니다.")
        bad = int(nums.isna().sum())
        if bad:
            report.rows_dropped_bad_time = bad
            report.warn(f"숫자로 읽지 못한 {bad}행을 제외했습니다.")
            df = df.loc[nums.notna()].copy()
            nums = nums.loc[nums.notna()]
        if nums.empty:
            raise CsvIngestError(f"'{time_col}' 에 쓸 수 있는 숫자가 없습니다.")
        # 음수는 문자열 정렬이 뒤집힌다(-1 이 -10 보다 앞선다). 경고로 넘기면
        # 순서가 조용히 깨지므로 여기서 갈라 처리한다.
        if (nums < 0).any():
            mode = cfg("csv.time.negative_index")
            if mode == "offset":
                shift = math.ceil(-nums.min())
                nums = nums + shift
                report.index_offset = shift
                report.warn(f"음수 인덱스가 있어 전체를 +{shift} 옮겨 저장했습니다 "
                            f"(원래 값 = 표시값 - {shift}).")
            else:
                raise CsvIngestError(
                    f"'{time_col}' 에 음수가 있어 순서를 보장할 수 없습니다. "
                    f"다른 열을 인덱스로 고르거나, config/app.yaml 의 "
                    f"csv.time.negative_index 를 offset 으로 바꾸세요.",
                    {"column": time_col, "min": float(nums.min())})

        # 정수부 자리수를 맞춰 0 을 채워야 문자열 정렬이 숫자 정렬과 같아진다
        has_frac = bool((nums % 1 != 0).any())
        width = max(len(str(abs(int(v)))) for v in (nums.min(), nums.max()))
        decimals = 6 if has_frac else 0
        pad = width + (decimals + 1 if decimals else 0)
        df[time_col] = nums.map(
            lambda v: f"{v:0{pad}.{decimals}f}" if decimals else f"{int(v):0{width}d}")
        report.time_format = f"0-padded({width}" + (f".{decimals}" if decimals else "") + ")"
        report.time_start, report.time_end = str(df[time_col].min()), str(df[time_col].max())

    else:  # text
        df[time_col] = df[time_col].astype(str)
        report.time_format = "text"
        report.warn("문자 인덱스는 사전순으로 정렬됩니다 — 시간순과 다를 수 있습니다.")

    return df


def _ambiguous_dayfirst(df: pd.DataFrame, col: str) -> bool:
    """1/2/2024 처럼 앞 두 자리가 모두 12 이하면 해석이 갈린다."""
    pat = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}")
    sample = df[col].astype(str).head(50)
    return any(pat.match(v) for v in sample)


# ── 5. 중복 타임스탬프 ────────────────────────────────────────
def resolve_duplicates(df: pd.DataFrame, time_col: str, value_cols: list,
                       policy: str, report: IngestReport) -> pd.DataFrame:
    """같은 시각이 여러 번 나오는 표를 정책대로 처리한다. 말없이 덮어쓰지 않는다."""
    report.duplicate_policy = policy
    dup_mask = df.duplicated(subset=[time_col], keep=False)
    n_dup = int(dup_mask.sum())
    if not n_dup:
        return df

    counts = df.loc[dup_mask, time_col].value_counts()
    limit = cfg("csv.duplicate_report_limit")
    report.duplicates_found = n_dup
    report.duplicate_examples = [
        {"timestamp": str(ts), "count": int(c)} for ts, c in counts.head(limit).items()
    ]

    if policy == "error":
        # 모든 시각이 똑같이 k번씩이면 '중복'이 아니라 같은 기간을 가진 파일 k개(지역·지점별)를
        # 한 축에 쌓은 것일 가능성이 크다 — 그때는 정책이 아니라 올리는 방식이 답이다.
        uniform = int(counts.mode().iloc[0]) if len(counts) else 0
        share = float((counts == uniform).mean()) if len(counts) else 0.0
        hint = ""
        if uniform >= 2 and share >= cfg("csv.duplicate_uniform_share") \
                and len(counts) >= df[time_col].nunique() * cfg("csv.duplicate_uniform_share"):
            hint = (f" 거의 모든 시각이 똑같이 {uniform}번씩 나옵니다 — 같은 기간을 가진 파일 {uniform}개"
                    f"(지역·지점이 다른 파일)를 합치기 모드로 올린 경우입니다. 이런 파일은 '개별' 모드로"
                    f" 올려 소스를 나누세요. 정책으로 합치면 지역 정보가 사라집니다.")
        raise CsvIngestError(
            f"같은 시각이 여러 번 나옵니다 — {len(counts)}개 시각, {n_dup}행."
            + (hint or " 중복 처리 방식을 고르고 다시 올리세요."),
            {"duplicate_timestamps": len(counts), "duplicate_rows": n_dup,
             "uniform_count": uniform if hint else None,
             "examples": report.duplicate_examples},
        )

    if policy in ("first", "last"):
        out = df.drop_duplicates(subset=[time_col], keep=policy)
        dropped = len(df) - len(out)
        report.warn(f"중복 {dropped}행을 버렸습니다({policy} 값 유지).")
        return out.reset_index(drop=True)

    if policy == "mean":
        num_cols = [c for c in value_cols if pd.api.types.is_numeric_dtype(df[c])]
        other = [c for c in df.columns if c not in num_cols and c != time_col]
        agg = {c: "mean" for c in num_cols}
        agg.update({c: "first" for c in other})
        out = df.groupby(time_col, as_index=False, sort=True).agg(agg)
        merged = len(df) - len(out)
        report.warn(f"중복 {merged}행을 평균으로 합쳤습니다.")
        return out

    if policy == "shift":
        interval = pd.Timedelta(report.sampling_interval) if report.sampling_interval else None
        if report.index_type != "datetime" or interval is None or interval <= pd.Timedelta(0):
            raise CsvIngestError(
                "시각을 밀어서 나누려면 날짜 인덱스와 일정한 샘플링 간격이 필요합니다. "
                "다른 중복 처리 방식을 고르세요.")
        # 한 칸 전체를 밀면 다음 시각과 부딪힌다. 간격을 겹친 수만큼 쪼개
        # 원래 시각과 다음 시각 사이에 집어넣는다.
        widest = int(df[time_col].value_counts().max())
        step = interval / widest
        if step <= pd.Timedelta(0):
            raise CsvIngestError("나눌 간격이 너무 좁습니다. 다른 중복 처리 방식을 고르세요.")
        ts = pd.to_datetime(df[time_col])
        offset = df.groupby(time_col).cumcount()
        df = df.copy()
        df[time_col] = ts + offset * step
        # 쪼갠 간격이 초보다 작으면 초 단위 형식으로는 도로 겹친다
        fmt = report.time_format
        if step < pd.Timedelta(seconds=1) and "%f" not in fmt:
            fmt = "%Y-%m-%d %H:%M:%S.%f"
            report.time_format = fmt
            report.warn("간격이 1초 미만이라 시각 형식에 소수점 이하를 더했습니다.")
        df[time_col] = df[time_col].dt.strftime(fmt)
        report.warn(f"중복 {n_dup}행을 {step} 씩 밀어 모두 남겼습니다.")
        if df.duplicated(subset=[time_col]).any():
            raise CsvIngestError("시각을 밀었는데도 중복이 남았습니다. 다른 방식을 고르세요.")
        return df.reset_index(drop=True)

    raise CsvIngestError(f"모르는 중복 처리 방식입니다: '{policy}'")


# ── 6. 값 열 정리 ─────────────────────────────────────────────
def coerce_values(df: pd.DataFrame, value_cols: list,
                  report: IngestReport) -> pd.DataFrame:
    """고른 값 열을 숫자로 맞춘다. 숫자가 아닌 칸은 결측으로 두고 개수를 보고한다."""
    df = df.copy()
    for col in value_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        before = df[col].notna().sum()
        df[col] = pd.to_numeric(
            df[col].astype(str).str.strip(),
            errors="coerce")
        lost = int(before - df[col].notna().sum())
        if lost:
            report.values_coerced += lost
            report.warn(f"'{col}' 의 숫자 아닌 {lost}칸을 결측으로 두었습니다.")
    return df


# ── 7. 한 번에 ────────────────────────────────────────────────
def prepare_for_import(df: pd.DataFrame, time_col: str, index_type: str,
                       value_cols: list, duplicate_policy: str,
                       report: IngestReport) -> pd.DataFrame:
    """시각 정규화 → 값 정리 → 중복 해소 → 시간순 정렬."""
    if time_col not in df.columns:
        raise CsvIngestError(f"시각 열 '{time_col}' 이 파일에 없습니다.",
                             {"columns": list(df.columns)})
    missing = [c for c in value_cols if c not in df.columns]
    if missing:
        raise CsvIngestError(f"고른 열이 파일에 없습니다: {', '.join(missing)}",
                             {"columns": list(df.columns)})

    df = normalize_time(df, time_col, index_type, report)
    df = coerce_values(df, value_cols, report)
    df = resolve_duplicates(df, time_col, value_cols, duplicate_policy, report)
    df = df.sort_values(time_col, kind="mergesort").reset_index(drop=True)

    keep = df[value_cols].notna().any(axis=1)
    dropped_empty = int((~keep).sum())
    if dropped_empty:
        report.warn(f"값이 하나도 없는 {dropped_empty}행을 제외했습니다.")
        df = df.loc[keep].reset_index(drop=True)

    if df.empty:
        raise CsvIngestError("정리하고 나니 저장할 행이 남지 않았습니다.")
    report.rows_stored = len(df)
    return df
