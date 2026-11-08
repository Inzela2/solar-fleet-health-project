"""Static checks on the Power BI project, so a renamed column or measure fails CI instead of
breaking the report silently. No Power BI or database needed."""
import json
import re
from pathlib import Path

PBI = Path(__file__).resolve().parents[1] / "powerbi"
MODEL = PBI / "SolarFleetHealth.SemanticModel" / "definition"
REPORT = PBI / "SolarFleetHealth.Report" / "definition"


def _name(raw: str) -> str:
    raw = raw.strip()
    return raw[1:-1].replace("''", "'") if raw.startswith("'") else raw


def load_model():
    """Return {table: {"columns": set, "measures": set}} parsed from the TMDL files."""
    model = {}
    for f in (MODEL / "tables").glob("*.tmdl"):
        text = f.read_text(encoding="utf-8")
        table = _name(re.search(r"^table (.+)$", text, re.M).group(1))
        cols = {_name(m) for m in re.findall(r"^\tcolumn (.+)$", text, re.M)}
        meas = {_name(m.split(" =")[0]) for m in re.findall(r"^\tmeasure (.+)$", text, re.M)}
        model[table] = {"columns": cols, "measures": meas, "text": text}
    return model


def visual_fields():
    for f in REPORT.rglob("visual.json"):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for role in doc.get("visual", {}).get("query", {}).get("queryState", {}).values():
            for p in role["projections"]:
                kind, ref = next(iter(p["field"].items()))
                yield f, kind, ref["Expression"]["SourceRef"]["Entity"], ref["Property"]


def test_every_visual_field_exists_in_the_model():
    model = load_model()
    for f, kind, table, prop in visual_fields():
        assert table in model, f"{f}: unknown table {table}"
        bucket = "measures" if kind == "Measure" else "columns"
        assert prop in model[table][bucket], f"{f}: {table}[{prop}] is not a {kind.lower()} in the model"


def test_relationships_point_at_real_columns():
    model = load_model()
    text = (MODEL / "relationships.tmdl").read_text(encoding="utf-8")
    pairs = re.findall(r"(?:from|to)Column: (\w+)\.(\w+)", text)
    assert len(pairs) == 8
    for table, column in pairs:
        assert column in model[table]["columns"], f"{table}.{column}"


def test_dax_references_resolve():
    model = load_model()
    all_measures = set().union(*(t["measures"] for t in model.values()))
    for table, t in model.items():
        dax = t["text"].split("\tpartition ")[0]          # partitions hold Power Query M, not DAX
        dax = re.sub(r"^\s*///.*$", "", dax, flags=re.M)  # descriptions
        for tbl, col in re.findall(r"\b(\w+)\[([^\]]+)\]", dax):
            if tbl in model and tbl != "_Measures":
                assert col in model[tbl]["columns"], f"{table}: {tbl}[{col}] does not exist"
        for ref in re.findall(r"(?<![\w\]'])\[([^\]]+)\]", dax):
            if ref.startswith("Value") or ref == "Placeholder":
                continue                      # calculated-table source columns
            assert ref in all_measures, f"{table}: measure [{ref}] does not exist"


def test_every_page_in_page_order_exists():
    order = json.loads((REPORT / "pages" / "pages.json").read_text())["pageOrder"]
    assert order and all((REPORT / "pages" / p / "page.json").exists() for p in order)


def test_model_tables_match_reporting_views():
    """Each imported table must be a view or table the SQL reporting layer actually creates."""
    sql = (Path(__file__).resolve().parents[1] / "sql" / "reporting_views.sql").read_text()
    created = set(re.findall(r"reporting\.(\w+)\s+(?:AS|\()", sql))
    imported = set()
    for t in load_model().values():
        imported |= set(re.findall(r'Item = "(\w+)"', t["text"]))
    assert imported and imported <= created, imported - created
