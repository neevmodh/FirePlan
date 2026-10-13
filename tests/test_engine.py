import io
import math

import ezdxf
import pytest
from pydantic import ValidationError

from fireplan.hydraulics import BAR_TO_METRES, Coverage, evaluate, simulate
from fireplan.ingestion import ingest_dxf
from fireplan.models import EvaluationRequest, Network, Settings
from fireplan.optimizer import optimize
from fireplan.samples import sample_network


@pytest.fixture
def chain():
    return Network.model_validate({"name": "Analytic pipe", "source_id": "S", "nodes": [
        {"id": "S", "x": 0, "y": 0, "elevation_m": 2, "candidate": False},
        {"id": "A", "x": 100, "y": 0, "elevation_m": 5},
        {"id": "B", "x": 200, "y": 0, "elevation_m": 8}],
        "pipes": [{"id": "SA", "start": "S", "end": "A", "length_m": 100, "diameter_mm": 100},
                  {"id": "AB", "start": "A", "end": "B", "length_m": 100, "diameter_mm": 100}],
        "zones": [{"id": "Z", "label": "Test zone", "risk": 9,
                   "polygon": [[95, -5], [205, -5], [205, 5], [95, 5]]}]})


def test_analytic_hazen_williams_and_elevation(chain):
    settings = Settings(outlet_demand_lps=5)
    result = simulate(chain, settings, ["B"])
    loss = 10.667 * 100 * .005 ** 1.852 / (120 ** 1.852 * .1 ** 4.871)
    expected = 6 - (6 + 2 * loss) / BAR_TO_METRES
    assert result["feasible"]
    assert result["min_active_pressure_bar"] == pytest.approx(expected, abs=2e-4)
    assert result["flows_lps"]["SA"] == pytest.approx(5)
    assert result["flows_lps"]["AB"] == pytest.approx(5)


def test_signed_reverse_pipe_flow(chain):
    reference = simulate(chain, Settings(), ["B"])
    chain.pipes[0].start, chain.pipes[0].end = "A", "S"
    reversed_result = simulate(chain, Settings(), ["B"])
    assert reversed_result["flows_lps"]["SA"] == pytest.approx(-5)
    assert reversed_result["min_active_pressure_bar"] == pytest.approx(reference["min_active_pressure_bar"], abs=1e-7)


@pytest.mark.parametrize("demand,diameter,roughness",[(5,100,120),(10,100,120),(5,150,120),(5,100,80)])
def test_nonlinear_friction_matches_flow_diameter_and_roughness(chain,demand,diameter,roughness):
    for pipe in chain.pipes:
        pipe.diameter_mm=diameter
        pipe.roughness=roughness
    settings=Settings(outlet_demand_lps=demand)
    result=simulate(chain,settings,["B"])
    expected_loss=2*10.667*100*(demand/1000)**1.852/(roughness**1.852*(diameter/1000)**4.871)
    friction_loss=(settings.source_pressure_bar-result["min_active_pressure_bar"])*BAR_TO_METRES-6
    assert friction_loss==pytest.approx(expected_loss,abs=.002)


def test_ga_keeps_pressure_constraint_hard_even_with_low_penalty(chain):
    settings=Settings(max_outlets=2,outlet_demand_lps=10,pump_capacity_lps=20,
                      min_pressure_bar=5.3,coverage_radius_m=60,penalty_weight=1,population=8,generations=3)
    assert simulate(chain,settings,["A"])["feasible"]
    assert not simulate(chain,settings,["A","B"])["feasible"]
    result=optimize(chain,settings)
    assert result["solution"]["placements"]==["A"]
    assert result["solution"]["min_active_pressure_bar"]>=settings.min_pressure_bar
    assert len(result["solution"]["placements"])<=settings.max_outlets
    assert result["solution"]["total_demand_lps"]<=settings.pump_capacity_lps


def test_minimum_pressure_is_a_hard_threshold_at_fixed_radius(chain):
    low = evaluate(chain, Settings(source_pressure_bar=7, min_pressure_bar=1.5), ["B"])
    high = evaluate(chain, Settings(source_pressure_bar=7, min_pressure_bar=6.5), ["B"])
    assert low["feasible"] and not high["feasible"]
    assert "minimum_pressure" in high["violations"]
    assert high["min_active_pressure_bar"] == pytest.approx(low["min_active_pressure_bar"])
    assert high["coverage_pct"] == low["coverage_pct"]
    assert high["constraints"]["pressure_margin_bar"] == pytest.approx(
        low["constraints"]["pressure_margin_bar"] - 5)


def test_source_pressure_changes_delivered_pressure_at_fixed_radius(chain):
    low = evaluate(chain, Settings(source_pressure_bar=1.5, min_pressure_bar=1), ["B"])
    high = evaluate(chain, Settings(source_pressure_bar=6.5, min_pressure_bar=1), ["B"])
    assert high["min_active_pressure_bar"] - low["min_active_pressure_bar"] == pytest.approx(5)
    assert high["coverage_pct"] == low["coverage_pct"]
    assert high["flows_lps"] == pytest.approx(low["flows_lps"])


def test_mass_balance_on_looped_network():
    network = sample_network()
    settings = Settings()
    active = network.existing_placements
    result = simulate(network, settings, active)
    assert result["feasible"]
    for node in network.nodes:
        if node.id == network.source_id:
            continue
        inflow = sum(result["flows_lps"][p.id] * (1 if p.end == node.id else -1)
                     for p in network.pipes if node.id in (p.start, p.end))
        expected = node.background_demand_lps + (settings.outlet_demand_lps if node.id in active else 0)
        assert inflow == pytest.approx(expected, abs=1e-7)


def test_budget_and_supply_are_separate_hard_constraints(chain):
    budget = simulate(chain, Settings(max_outlets=1), ["A", "B"])
    assert "outlet_budget" in budget["violations"]
    chain.nodes[1].background_demand_lps = 2
    capacity = simulate(chain, Settings(pump_capacity_lps=6), ["B"])
    assert capacity["total_demand_lps"] == 7
    assert "supply_capacity" in capacity["violations"]


def test_inactive_nodes_cannot_have_negative_pressure(chain):
    chain.nodes[2].elevation_m = 1000
    result = simulate(chain, Settings(), ["A"])
    assert not result["feasible"]
    assert "negative_network_pressure" in result["violations"]


def test_overlapping_outlet_coverage_counted_once(chain):
    coverage = Coverage(chain, 200)
    single = coverage.calculate(["A"])
    duplicate_area = coverage.calculate(["A", "B"])
    assert single["coverage_pct"] == pytest.approx(100)
    assert single["weighted_score"] == pytest.approx(duplicate_area["weighted_score"])
    assert duplicate_area["weighted_score"] == pytest.approx(9 * 1100)


def test_optimizer_never_returns_infeasible_layout(chain):
    settings = Settings(source_pressure_bar=2, min_pressure_bar=3.5, population=8, generations=2)
    result = optimize(chain, settings)
    assert result["status"] == "no_feasible_layout_found"
    assert result["solution"] is None
    assert result["best_attempt"] is not None
    assert not result["best_attempt"]["feasible"]


def test_capacity_can_force_fewer_than_k_outlets(chain):
    result = optimize(chain, Settings(max_outlets=2, pump_capacity_lps=5, population=8, generations=2))
    assert result["solution"]["feasible"]
    assert len(result["solution"]["placements"]) == 1
    assert result["solution"]["total_demand_lps"] <= 5


def test_seeded_search_is_repeatable_and_retains_best():
    network, settings = sample_network(), Settings(population=8, generations=3)
    first = optimize(network, settings)
    second = optimize(network, settings)
    assert first["solution"]["placements"] == second["solution"]["placements"]
    assert first["history"] == second["history"]
    coverage = [h["coverage_pct"] for h in first["history"]]
    assert coverage == sorted(coverage)
    assert first["solution"]["min_active_pressure_bar"] >= settings.min_pressure_bar
    assert first["solution"]["coverage_pct"] > evaluate(network, settings, network.existing_placements)["coverage_pct"]


def make_dxf(lines, risk=True):
    doc = ezdxf.new()
    doc.layers.add("PIPES")
    doc.layers.add("RISK_9")
    for a, b in lines:
        doc.modelspace().add_line(a, b, dxfattribs={"layer": "PIPES"})
    if risk:
        doc.modelspace().add_lwpolyline([(-5, -5), (105, -5), (105, 105), (-5, 105)], close=True,
                                      dxfattribs={"layer": "RISK_9"})
    output = io.StringIO()
    doc.write(output)
    return output.getvalue().encode()


def test_dxf_splits_crossings_and_converts_units():
    content = make_dxf([((0, 50), (100, 50)), ((50, 0), (50, 100))])
    network = ingest_dxf(content, metres_per_unit=.01, source_x=0, source_y=50)
    assert len(network.nodes) == 5
    assert len(network.pipes) == 4
    assert all(p.length_m == pytest.approx(.5) for p in network.pipes)
    assert sum(not n.candidate for n in network.nodes) == 1
    assert network.zones[0].risk == 9


def test_dxf_rejects_disconnected_pipes():
    content = make_dxf([((0, 0), (10, 0)), ((90, 0), (100, 0))])
    with pytest.raises(ValueError, match="disconnected"):
        ingest_dxf(content, metres_per_unit=1, source_x=0, source_y=0)


def test_dxf_requires_risk_and_explicit_supply():
    content = make_dxf([((0, 0), (100, 0))], risk=False)
    with pytest.raises(ValueError, match="RISK"):
        ingest_dxf(content, metres_per_unit=1, source_x=0, source_y=0)
    content = make_dxf([((0, 0), (100, 0))])
    with pytest.raises(ValueError, match="Source must match"):
        ingest_dxf(content, metres_per_unit=1, source_x=50, source_y=1)


def test_schema_rejects_duplicates_nonfinite_and_disconnection(chain):
    with pytest.raises(ValidationError):
        Settings(source_pressure_bar=math.nan)
    with pytest.raises(ValidationError, match="unique"):
        EvaluationRequest(network=chain, placements=["A", "A"])
    data = chain.model_dump()
    data["nodes"].append({"id": "C", "x": 500, "y": 0})
    with pytest.raises(ValidationError, match="connect"):
        Network.model_validate(data)
