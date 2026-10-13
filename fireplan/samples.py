from .models import Network


def sample_network(key: str = "vatva") -> Network:
    """Synthetic teaching networks; no measured industrial belt data."""
    naroda = key == "naroda"
    nodes = [{"id": "SUPPLY", "x": -22, "y": 52.5, "candidate": False, "label": "Water supply"}]
    for row in range(4):
        for col in range(6):
            nodes.append({"id": f"N{row * 6 + col + 1:02d}", "x": col * 35, "y": row * 35,
                          "elevation_m": round(col * 0.4 + row * 0.2, 2),
                          "label": f"Junction {row * 6 + col + 1:02d}"})
    pipes = [{"id": "P00", "start": "SUPPLY", "end": "N07", "length_m": 28.1,
              "diameter_mm": 125, "roughness": 120}]
    for row in range(4):
        for col in range(6):
            idx = row * 6 + col + 1
            for other in ([idx + 1] if col < 5 else []) + ([idx + 6] if row < 3 else []):
                pipes.append({"id": f"P{len(pipes):02d}", "start": f"N{idx:02d}", "end": f"N{other:02d}",
                              "length_m": 35, "diameter_mm": 80 if col >= 3 else 100, "roughness": 120})
    labels = ["Chemical storage", "Processing unit", "Packaging", "Solvent handling", "Assembly", "Dispatch"]
    risks = [9.4, 8.2, 4.6, 9.0, 5.8, 3.2] if not naroda else [7.8, 5.4, 8.9, 6.5, 9.2, 4.0]
    zones = []
    for row in range(2):
        for col in range(3):
            idx = row * 3 + col
            x, y = col * 70 - 10, row * 70 - 10
            zones.append({"id": f"Z{idx+1}", "label": labels[idx], "risk": risks[idx],
                          "polygon": [[x, y], [x + 55, y], [x + 55, y + 45], [x, y + 45]]})
    return Network.model_validate({"name": "Naroda industrial estate" if naroda else "Vatva industrial estate",
        "description": "Synthetic single-level hydrant network for exploring placement tradeoffs.",
        "synthetic": True, "source_id": "SUPPLY", "nodes": nodes, "pipes": pipes, "zones": zones,
        "existing_placements": ["N01", "N06", "N19", "N24"]})
