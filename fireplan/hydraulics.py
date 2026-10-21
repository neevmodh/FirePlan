from __future__ import annotations

import math
import warnings

import wntr
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .models import Network, Settings

BAR_TO_METRES = 100000 / (1000 * 9.80665)


class Coverage:
    """Risk-weighted circle union clipped to actual zone boundaries.

    Circles use inscribed 512-sided polygons (128 segments per quadrant).
    A full circle's area underestimation is less than 0.00251%; narrow or
    concave zones no longer depend on whether a sampling grid hits them.
    """
    CIRCLE_SEGMENTS = 512

    def __init__(self, network: Network, radius_m: float):
        self.polygons = {z.id: Polygon(z.polygon) for z in network.zones}
        self.risks = {z.id: z.risk for z in network.zones}
        self.zone_weights = {z.id: self.polygons[z.id].area * z.risk for z in network.zones}
        self.total = math.fsum(self.zone_weights.values())
        self.footprints = {}
        for node in network.nodes:
            if node.candidate:
                disk = Point(node.x, node.y).buffer(radius_m, quad_segs=self.CIRCLE_SEGMENTS // 4)
                self.footprints[node.id] = {key: polygon.intersection(disk)
                                            for key, polygon in self.polygons.items()}
        self.cache = {}

    def calculate(self, placements: tuple | list):
        key = tuple(sorted(set(placements)))
        if key in self.cache:
            return self.cache[key]
        areas = {}
        for zone, polygon in self.polygons.items():
            pieces = [self.footprints[node][zone] for node in key
                      if not self.footprints[node][zone].is_empty]
            # A disk that covers the whole zone makes further unions unnecessary.
            if any(piece.area >= polygon.area for piece in pieces):
                area = polygon.area
            else:
                area = unary_union(pieces).area if pieces else 0.0
            areas[zone] = min(polygon.area, max(0.0, area))
        score = math.fsum(areas[zone] * self.risks[zone] for zone in areas)
        result = {"weighted_score": score,
                  "coverage_pct": min(100.0, max(0.0, 100 * score / self.total)) if self.total else 0.0,
                  "zones": {zone: min(100.0, 100 * area / self.polygons[zone].area)
                            for zone, area in areas.items()},
                  "covered_area_m2": areas,
                  "coverage_method": "Circle unions clipped to CAD risk polygons (512 segments per circle)"}
        # Bound memoization even when greedy initialization explores many combinations.
        if len(self.cache) >= 4096:
            self.cache.clear()
        self.cache[key] = result
        return result


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
    demands = {}
    for node in network.nodes:
        if node.id == source.id:
            continue
        demand = node.background_demand_lps + (settings.outlet_demand_lps if node.id in active else 0)
        total_lps += demand
        demands[node.id] = demand
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
        if set(pressures) != {node.id for node in network.nodes} or set(flows) != {pipe.id for pipe in network.pipes}:
            raise ValueError("Incomplete hydraulic solution")
        net_inflow = {node.id: 0.0 for node in network.nodes}
        for pipe in network.pipes:
            net_inflow[pipe.start] -= flows[pipe.id]
            net_inflow[pipe.end] += flows[pipe.id]
        mass_error = max((abs(net_inflow[node] - demand) for node, demand in demands.items()), default=0.0)
        source_error = abs(-net_inflow[source.id] - total_lps)
        if max(mass_error, source_error) > 1e-5:
            raise ValueError("Hydraulic flow continuity check failed")
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
                "hydraulic_validation": {"max_junction_flow_error_lps": mass_error,
                                         "source_flow_error_lps": source_error,
                                         "flow_tolerance_lps": 1e-5},
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
