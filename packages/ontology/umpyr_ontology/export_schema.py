"""Export JSON Schema for every ontology model to packages/ontology/schema/.

Run: uv run python -m umpyr_ontology.export_schema
"""

import json
from pathlib import Path

from . import models
from .catalog import FIELD_CATALOG

EXPORTED = [
    models.Customer,
    models.Item,
    models.Shipment,
    models.Order,
    models.Flight,
    models.Bag,
    models.Booking,
    models.Claim,
    models.Case,
    models.Facts,
    models.Ticket,
]


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for model in EXPORTED:
        path = out_dir / f"{model.__name__}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n")
        written.append(path)
    catalog = out_dir / "field_catalog.json"
    catalog.write_text(
        json.dumps(
            [
                {"path": f.path, "type": f.type, "description": f.description, "enum": list(f.enum_values)}
                for f in FIELD_CATALOG
            ],
            indent=2,
        )
        + "\n"
    )
    written.append(catalog)
    return written


if __name__ == "__main__":
    here = Path(__file__).resolve().parent.parent / "schema"
    for p in export(here):
        print(p)
