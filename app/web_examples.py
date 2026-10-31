"""API-ready versions of the existing educational examples."""

import json
from pathlib import Path

import wb_core as wb


def preset_request(rows: dict, tail_id: str) -> dict:
    items = [dict(name=name, weight=w, cg=x) for name, w, x in rows["fixed"]]
    grouped: dict[str, list[tuple[float, float]]] = {}
    for name, weight, cg in rows["items"]:
        grouped.setdefault(name, []).append((weight, cg))
    for name, choices in grouped.items():
        present = [(w, x) for w, x in choices if w != 0]
        weights, cgs = [w for w, _ in present], [x for _, x in present]
        # These presets use Cartesian endpoint choices; don't generalise this to arbitrary choices.
        assert set(present) == set((w, x) for w in weights for x in cgs)
        items.append(dict(
            name=name, weight=(min(weights) + max(weights)) / 2,
            weight_delta=(max(weights) - min(weights)) / 2,
            cg=(min(cgs) + max(cgs)) / 2, cg_delta=(max(cgs) - min(cgs)) / 2,
            optional=len(present) != len(choices),
        ))
    fuel = [dict(weight=0, moment=0)]
    for _, weight, arm in rows["tanks"]:
        fuel.append(dict(weight=fuel[-1]["weight"] + weight,
                         moment=fuel[-1]["moment"] + weight * arm))
    return dict(
        envelope=[dict(cg=x, weight=w) for x, w in rows["envelope"]],
        tails=[dict(id=tail_id, weight=w, cg=x) for _, w, x in rows["basics"]],
        items=items, fuel_flow=fuel, options=dict(all_centrograms=False, max_steps=5000),
    )


def example_requests() -> list[dict]:
    light_twin = json.loads((Path(__file__).resolve().parent.parent / "examples" / "light_twin.json").read_text(encoding="utf-8"))
    return [
        dict(id="light-twin", label="Light Twin", units=dict(weight="lb", cg="in"), request=light_twin),
        dict(id="boeing-707", label="Boeing 707-124", units=dict(weight="lb", cg="in"),
             request=preset_request(wb.preset_707(), "DEMO-707 (illustrative)")),
        dict(id="toy", label="Sloped aft limit · toy example", units=None,
             request=preset_request(wb.preset_toy(), "N-TOY (illustrative)")),
    ]
