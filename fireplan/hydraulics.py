from __future__ import annotations

import math
import warnings

import wntr
from shapely.geometry import Point, Polygon

from .models import Network, Settings

BAR_TO_METRES = 100000 / (1000 * 9.80665)


class Coverage:
    """Area-weighted midpoint quadrature, clipped to polygon interiors.

    Each zone is sampled on a 12x12 grid. Each covered sample contributes once
    within that zone. Overlapping zone polygons remain separate risk layers.
    """

    def __init__(self, network: Network, radius_m: float):
        self.zone_weights = {}
        self.samples = []
        for zone in network.zones:
            polygon = Polygon(zone.polygon)
            x0, y0, x1, y1 = polygon.bounds
            points = [Point(x0 + (i + 0.5) * (x1 - x0) / 12,
                            y0 + (j + 0.5) * (y1 - y0) / 12)
                      for i in range(12) for j in range(12)]
            points = [point for point in points if polygon.covers(point)]
            if not points:
                points = [polygon.representative_point()]
            weight = polygon.area * zone.risk / len(points)
            self.zone_weights[zone.id] = polygon.area * zone.risk
            self.samples.extend((zone.id, point.x, point.y, weight) for point in points)
        self.total = sum(self.zone_weights.values())
        self.masks = {}
        for node in network.nodes:
            if node.candidate:
                self.masks[node.id] = {i for i, (_, x, y, _) in enumerate(self.samples)
                                       if math.hypot(x - node.x, y - node.y) <= radius_m}

    def calculate(self, placements: tuple | list):
        covered = set().union(*(self.masks[node] for node in placements)) if placements else set()
        score = sum(self.samples[i][3] for i in covered)
        by_zone = {}
        for i in covered:
            zone, _, _, weight = self.samples[i]
            by_zone[zone] = by_zone.get(zone, 0) + weight
        return {"weighted_score": score,
                "coverage_pct": 100 * score / self.total if self.total else 0,
                "zones": {zone: 100 * by_zone.get(zone, 0) / total if total else 0
                          for zone, total in self.zone_weights.items()}}


def simulate(network: Network, settings: Settings, placements: list | tuple) -> dict:
    """Fixed-demand, simultaneous-outlet design check at t=0.

    The source is a constant-head boundary; capacity is enforced separately.
    WNTRSimulator solves mass balance and signed H-W energy equations.
    """
    wn = wntr.network.WaterNetworkModel()
    wn.options.time.duration = 0
    wn.options.hydraulic.headloss = "H-W"
    wn.options.hydraulic.demand_model = "DD"
    active = set(placements)
    source = next(n for n in network.nodes if n.id == network.source_id)
    wn.add_reservoir(source.id, base_head=source.elevation_m + settings.source_pressure_bar * BAR_TO_METRES,
                     coordinates=(source.x, source.y))
    total_lps = 0
    for node in network.nodes:
        if node.id == source.id:
            continue
        demand = node.background_demand_lps + (settings.outlet_demand_lps if node.id in active else 0)
        total_lps += demand
        wn.add_junction(node.id, base_demand=demand / 1000, elevation=node.elevation_m,
                        coordinates=(node.x, node.y))
    for pipe in network.pipes:
        wn.add_pipe(pipe.id, pipe.start, pipe.end, length=pipe.length_m,
                    diameter=(settings.diameter_override_mm or pipe.diameter_mm) / 1000,
                    roughness=pipe.roughness, minor_loss=pipe.minor_loss)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            result = wntr.sim.WNTRSimulator(wn).run_sim(convergence_error=True)
        pressures = {str(node): float(head) / BAR_TO_METRES
                     for node, head in result.node["pressure"].iloc[0].items()}
        pressures[source.id] = settings.source_pressure_bar
        flows = {str(pipe): float(flow) * 1000 for pipe, flow in result.link["flowrate"].iloc[0].items()}
        if not all(math.isfinite(value) for value in [*pressures.values(), *flows.values()]):
            raise ValueError("Nonfinite hydraulic solution")
        pressure_violation = sum(max(0, settings.min_pressure_bar - pressures[node]) for node in active)
        violations = []
        if len(active) > settings.max_outlets:
            violations.append("outlet_budget")
        if not set(network.required_placements).issubset(active):
            violations.append("required_outlets_missing")
        if total_lps > settings.pump_capacity_lps + 1e-8:
            violations.append("supply_capacity")
        if pressure_violation > 1e-6:
            violations.append("minimum_pressure")
        # Inactive junctions still require a nonnegative water pressure.
        if any(pressures[n.id] < -1e-6 for n in network.nodes if n.id != source.id):
            violations.append("negative_network_pressure")
        return {"feasible": not violations, "violations": violations, "pressures_bar": pressures,
                "flows_lps": flows, "total_demand_lps": total_lps,
                "min_active_pressure_bar": min((pressures[n] for n in active), default=None),
                "pressure_deficit_bar": pressure_violation,
                "engine": "WNTRSimulator / Hazen–Williams / fixed demand"}
    except Exception as error:
        return {"feasible": False, "violations": ["hydraulic_solver_failure"], "pressures_bar": {},
                "flows_lps": {}, "total_demand_lps": total_lps, "min_active_pressure_bar": None,
                "pressure_deficit_bar": settings.min_pressure_bar * len(active),
                "engine": "WNTRSimulator", "solver_error": str(error)[:300]}


def evaluate(network: Network, settings: Settings, placements: list, coverage: Coverage | None = None):
    cov = (coverage or Coverage(network, settings.coverage_radius_m)).calculate(placements)
    result = {**simulate(network, settings, placements), **cov, "placements": placements}
    result["missing_required_outlets"] = sorted(set(network.required_placements) - set(placements))
    result["required_outlet_count"] = len(network.required_placements)
    result["constraints"] = constraint_diagnostics(result, settings)
    return result


def constraint_diagnostics(result: dict, settings: Settings) -> dict:
    """Dimensionless squared exterior penalties plus interpretable design slack."""
    active = result["placements"]
    pressures = result["pressures_bar"]
    failed = "hydraulic_solver_failure" in result["violations"]
    pressure_margin = (result["min_active_pressure_bar"] - settings.min_pressure_bar
                       if result["min_active_pressure_bar"] is not None else None)
    capacity_margin = settings.pump_capacity_lps - result["total_demand_lps"]
    budget_margin = settings.max_outlets - len(active)
    components = {
        "pressure": sum((max(0, settings.min_pressure_bar - pressures[n]) / settings.min_pressure_bar) ** 2
                        for n in active if n in pressures),
        "capacity": (max(0, -capacity_margin) / settings.pump_capacity_lps) ** 2,
        "budget": (max(0, -budget_margin) / settings.max_outlets) ** 2,
        "nonnegative_pressure": sum((max(0, -p) / settings.min_pressure_bar) ** 2 for p in pressures.values()),
        "required_outlets": (len(result.get("missing_required_outlets", [])) / max(1, result.get("required_outlet_count", 0))) ** 2,
    }
    penalty = settings.penalty_weight * sum(components.values())
    score = None if failed else result["coverage_pct"] - penalty
    required_increase = max(0, -(pressure_margin or 0), -min(pressures.values(), default=0)) if not failed and active else None
    return {"pressure_margin_bar": pressure_margin, "capacity_margin_lps": capacity_margin,
        "budget_margin_outlets": budget_margin,
        "binding_constraints": (["minimum_pressure"] if pressure_margin is not None and abs(pressure_margin) <= .05 else [])
            + (["supply_capacity"] if abs(capacity_margin) <= .01 else [])
            + (["outlet_budget"] if budget_margin == 0 else []),
        "normalized_violation": sum(components.values()), "penalty_components": components,
        "penalty_weight": settings.penalty_weight, "penalty_value": penalty,
        "penalized_score": score, "solver_valid": not failed,
        "required_source_pressure_bar": settings.source_pressure_bar + required_increase + .05
            if required_increase is not None and required_increase > 1e-6 else None,
        "formula": "coverage_pct - lambda * sum(normalized positive constraint violations squared)"}
