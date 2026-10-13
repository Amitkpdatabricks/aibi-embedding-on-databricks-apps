import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PART1 = ROOT / "01-external-embedding/dashboard/sample.lvdash.json"
PART2 = ROOT / "02-group-based-rls/dashboard/sample_rls.lvdash.json"
PREDICATE = "array_contains(split(__aibi_external_value, ','), upper(r.r_name))"


def queries(path):
    d = json.loads(path.read_text())
    assert d["pages"], "dashboard has no pages"
    return ["\n".join(ds.get("queryLines", [ds.get("query", "")])) for ds in d["datasets"]]


def test_part1_reads_samples_without_rls():
    qs = queries(PART1)
    assert all("samples.tpch" in q for q in qs)
    assert not any("__aibi_external_value" in q for q in qs)


def test_part2_every_dataset_has_rls_predicate():
    qs = queries(PART2)
    assert qs and all(PREDICATE in q and "nullif(__aibi_external_value, '') IS NULL" in q for q in qs)


def test_every_page_has_widgets():
    for path in (PART1, PART2):
        pages = json.loads(path.read_text())["pages"]
        assert pages and all(p["layout"] for p in pages), f"{path.name} has an empty page"
