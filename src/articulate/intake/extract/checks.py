"""Deterministic code checks on Extract output (§3.5, #22, #61)."""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckReport:
    rejected: list[str] = field(default_factory=list)
    logged: list[str] = field(default_factory=list)
    uncontrasted_pairs: int = 0


def _has_control_char(text: str) -> bool:
    return bool(re.search(r"[\t\x08\x0c]", text))


def _has_stray_backslash(text: str) -> bool:
    return bool(re.search(r"\\\\(?=[)_$]|$)", text))


def _has_unbalanced_dollars(text: str) -> bool:
    return text.count("$") % 2 != 0


def _check_string(text: str, location: str, report: CheckReport) -> bool:
    """Return False if string has fatal format issues."""
    if _has_control_char(text):
        report.rejected.append(f"{location}: control char in: {text[:60]!r}")
        return False
    if _has_stray_backslash(text):
        report.rejected.append(f"{location}: stray \\\\ in: {text[:60]!r}")
        return False
    if _has_unbalanced_dollars(text):
        report.rejected.append(f"{location}: unbalanced $ in: {text[:60]!r}")
        return False
    return True


def check_output(
    data: dict[str, Any], n_pages: int | None = None
) -> tuple[dict[str, Any], CheckReport]:
    """Validate and filter Extract output according to rules in #22."""
    report = CheckReport()
    kept_concepts: list[dict[str, Any]] = []

    for raw_c in data.get("concepts", []):
        c = dict(raw_c)
        key = c.get("key", "")
        title = c.get("title", "")
        kind = c.get("kind", "definition")

        # 1. Check title
        if not _check_string(title, key, report):
            continue

        # 2. Check explanation (Claim)
        exp = c.get("explanation") or {}
        exp_text = exp.get("text", "")
        if not _check_string(exp_text, f"{key}.explanation", report):
            continue

        exp_cites = list(exp.get("citations", []))
        is_gap_fill = bool(exp.get("gapFill"))
        if is_gap_fill:
            if exp_cites:
                report.rejected.append(f"{key}.explanation: gapFill claim has citations")
                continue
        else:
            if not exp_cites:
                report.rejected.append(f"{key}.explanation: cited claim with no citation")
                continue
            if len(exp_cites) > 3:
                report.logged.append(
                    f"{key}.explanation: {len(exp_cites)} citations (extra citations capped to 3)"
                )
                exp_cites = exp_cites[:3]
                exp["citations"] = exp_cites

            if n_pages is not None:
                bad_cite = False
                for ct in exp_cites:
                    p = ct.get("page", 0)
                    if not (1 <= p <= n_pages):
                        report.rejected.append(f"{key}.explanation: page {p} out of range")
                        bad_cite = True
                        break
                if bad_cite:
                    continue

        # 3. Check method fields on non-methods
        if kind != "method":
            if (
                c.get("aliases")
                or c.get("useWhen")
                or c.get("conditions")
                or c.get("workedExamples")
            ):
                report.rejected.append(f"{key}: method fields on a {kind}")
                c["aliases"] = []
                c["useWhen"] = []
                c["conditions"] = []
                c["workedExamples"] = []
        else:
            # 4. Check method claims
            for f_name in ("useWhen", "conditions", "workedExamples"):
                kept_claims = []
                for i, cl in enumerate(c.get(f_name, [])):
                    where = f"{key}.{f_name}[{i}]"
                    cl_text = cl.get("text", "")
                    if not _check_string(cl_text, where, report):
                        continue
                    cl_cites = list(cl.get("citations", []))
                    if cl.get("gapFill"):
                        if cl_cites:
                            report.rejected.append(f"{where}: gapFill claim has citations")
                            continue
                    else:
                        if not cl_cites:
                            report.rejected.append(f"{where}: cited claim with no citation")
                            continue
                        if len(cl_cites) > 3:
                            report.logged.append(
                                f"{where}: {len(cl_cites)} citations (extra citations capped to 3)"
                            )
                            cl_cites = cl_cites[:3]
                            cl["citations"] = cl_cites
                        if n_pages is not None:
                            bad_cl_cite = False
                            for ct in cl_cites:
                                p = ct.get("page", 0)
                                if not (1 <= p <= n_pages):
                                    report.rejected.append(f"{where}: page {p} out of range")
                                    bad_cl_cite = True
                                    break
                            if bad_cl_cite:
                                continue
                    kept_claims.append(cl)
                c[f_name] = kept_claims

        # 5. Confidence check
        conf = c.get("confidence", "high")
        conf_note = c.get("confidenceNote", "")
        if conf != "high" and not conf_note.strip():
            report.logged.append(f"{key}: {conf} confidence without a note")

        kept_concepts.append(c)

    # 6. Contrast groups check
    valid_keys = {c["key"] for c in kept_concepts}
    kept_groups: list[dict[str, Any]] = []

    for raw_g in data.get("contrastGroups", []):
        g = dict(raw_g)
        g_name = g.get("name", "")
        members = list(g.get("memberKeys", []))

        # Check unknown members
        valid_members = []
        for mk in members:
            if mk not in valid_keys:
                report.rejected.append(f"group {g_name}: unknown member {mk}")
            else:
                valid_members.append(mk)

        if len(valid_members) < 2:
            continue

        g["memberKeys"] = valid_members
        m_set = set(valid_members)

        # Check contrasts
        valid_contrasts = []
        covered_pairs: set[frozenset[str]] = set()

        for ct in g.get("contrasts", []):
            a, b = ct.get("a", ""), ct.get("b", "")
            if a not in m_set or b not in m_set:
                report.rejected.append(f"group {g_name}: contrast {a}/{b} not both members")
                continue
            df = ct.get("decidingFeature") or {}
            df_text = df.get("text", "")
            if not _check_string(df_text, f"group {g_name} contrast {a}/{b}", report):
                continue
            if df.get("gapFill"):
                report.rejected.append(f"group {g_name}: gapFill contrast {a}/{b}")
                continue
            df_cites = list(df.get("citations", []))
            if not df_cites:
                report.rejected.append(
                    f"group {g_name} contrast {a}/{b}: decidingFeature without citations"
                )
                continue
            if len(df_cites) > 3:
                report.logged.append(
                    f"group {g_name} contrast {a}/{b}: extra citations capped to 3"
                )
                df_cites = df_cites[:3]
                df["citations"] = df_cites
            if n_pages is not None:
                bad_df_cite = False
                for cit in df_cites:
                    p = cit.get("page", 0)
                    if not (1 <= p <= n_pages):
                        report.rejected.append(
                            f"group {g_name} contrast {a}/{b}: page {p} out of range"
                        )
                        bad_df_cite = True
                        break
                if bad_df_cite:
                    continue

            valid_contrasts.append(ct)
            covered_pairs.add(frozenset((a, b)))

        g["contrasts"] = valid_contrasts

        # Uncontrasted pairs check
        all_pairs = {
            frozenset((a, b)) for i, a in enumerate(valid_members) for b in valid_members[i + 1 :]
        }
        uncontrasted = all_pairs - covered_pairs
        for pair in uncontrasted:
            pair_list = sorted(pair)
            report.logged.append(
                f"group {g_name}: pair {pair_list[0]}/{pair_list[1]} without a contrast"
            )
            report.uncontrasted_pairs += 1

        kept_groups.append(g)

    return {"concepts": kept_concepts, "contrastGroups": kept_groups}, report
