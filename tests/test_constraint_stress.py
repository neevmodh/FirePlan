"""Boundary, independent hydraulic-oracle, and full CAD setting regressions."""
import itertools
import io
import math
from pathlib import Path

import pytest
import ezdxf
from ezdxf.math import Matrix44
from fastapi.testclient import TestClient

from fireplan.app import app
from fireplan.hydraulics import BAR_TO_METRES, Coverage, evaluate
from fireplan.ingestion import ingest_dxf
from fireplan.models import Network, Settings
from fireplan.optimizer import optimize

GRID = Path(__file__).parent / 'fixtures' / 'industrial-grid.dxf'


@pytest.fixture
def tree():
    return Network.model_validate({
        'name': 'Exhaustive analytic reference', 'source_id': 'S',
        'nodes': [{'id': 'S', 'x': 0, 'y': 0, 'candidate': False}] +
                 [{'id': f'N{i}', 'x': i * 75, 'y': 0, 'elevation_m': i * 2,
                   'background_demand_lps': .2} for i in range(1, 4)],
        'pipes': [{'id': f'P{i}', 'start': 'S' if i == 1 else f'N{i-1}',
                   'end': f'N{i}', 'length_m': 75, 'diameter_mm': 100} for i in range(1, 4)],
        'zones': [{'id': f'Z{i}', 'label': f'Risk {i}', 'risk': i,
                   'polygon': [[i*75-2,-2],[i*75+2,-2],[i*75+2,2],[i*75-2,2]]}
                  for i in range(1, 4)]})


def analytic(tree, settings, active):
    """Independent downstream demand sums and closed-form tree head loss."""
    nodes = tree.nodes[1:]
    flows = [sum(n.background_demand_lps + settings.outlet_demand_lps * (n.id in active)
                 for n in nodes[i:]) for i in range(3)]
    losses = [10.667 * 75 * (q / 1000)**1.852 /
              (120**1.852 * ((settings.diameter_override_mm or 100)/1000)**4.871)
              for q in flows]
    pressures = {node.id: settings.source_pressure_bar -
                 (sum(losses[:i+1]) + node.elevation_m) / BAR_TO_METRES
                 for i, node in enumerate(nodes)}
    feasible = (len(active) <= settings.max_outlets and flows[0] <= settings.pump_capacity_lps + 1e-8 and
                all(pressures[n] >= settings.min_pressure_bar for n in active) and
                min(pressures.values()) >= 0 and set(tree.required_placements).issubset(active))
    # At radius=10 each outlet covers only its own 4x4 square, with risks 1,2,3.
    score = sum(16 * int(n[1:]) for n in active)
    return flows, pressures, feasible, score


@pytest.mark.parametrize('diameter,demand', list(itertools.product([50,100,200],[1,5,20])))
def test_tree_matches_independent_energy_and_mass_balance(tree, diameter, demand):
    settings=Settings(source_pressure_bar=7,diameter_override_mm=diameter,outlet_demand_lps=demand)
    active=['N1','N3']
    flows, pressures, feasible, _ = analytic(tree,settings,active)
    result=evaluate(tree,settings,active)
    assert result['constraints']['solver_valid']
    assert result['flows_lps']==pytest.approx(dict(zip(['P1','P2','P3'],flows)),abs=1e-6)
    assert result['pressures_bar']==pytest.approx({'S':7,**pressures},abs=.002)
    assert result['feasible']==feasible


@pytest.mark.parametrize('overrides', [
    {}, {'max_outlets':1}, {'max_outlets':2}, {'pump_capacity_lps':5.6},
    {'pump_capacity_lps':.59}, {'min_pressure_bar':6.5}, {'source_pressure_bar':1.5},
    {'diameter_override_mm':50}, {'outlet_demand_lps':20},
    {'min_pressure_bar':6.5,'penalty_weight':1},
])
def test_ga_matches_exhaustive_analytic_optimum_on_small_network(tree, overrides):
    settings=Settings(**({'source_pressure_bar':7,'coverage_radius_m':10,'max_outlets':3,
                         'population':32,'generations':12} | overrides))
    feasible=[]
    for bits in itertools.product([0,1],repeat=3):
        active=[f'N{i+1}' for i,bit in enumerate(bits) if bit]
        if active:
            _,_,valid,score=analytic(tree,settings,active)
            if valid:
                feasible.append((score,-len(active)))
    result=optimize(tree,settings)
    if not feasible:
        assert result['solution'] is None
    else:
        solution=result['solution']
        assert solution is not None and solution['feasible']
        assert (solution['weighted_score'],-len(solution['placements']))==pytest.approx(max(feasible))
        assert evaluate(tree,settings,solution['placements'])['feasible']


def test_pressure_and_capacity_at_and_across_the_exact_boundary(tree):
    settings=Settings(source_pressure_bar=7)
    reference=evaluate(tree,settings,['N3'])
    pressure=reference['min_active_pressure_bar']
    demand=reference['total_demand_lps']
    for delta, expected in [(0,True),(-.0001,True),(.0001,False)]:
        check=evaluate(tree,settings.model_copy(update={'min_pressure_bar':pressure+delta}),['N3'])
        assert check['feasible']==expected
    for delta, expected in [(0,True),(.0001,True),(-.0001,False)]:
        check=evaluate(tree,settings.model_copy(update={'pump_capacity_lps':demand+delta}),['N3'])
        assert check['feasible']==expected


def test_required_outlets_cannot_be_removed_to_fit_budget_or_pressure(tree):
    tree.required_placements=['N1','N3']
    result=optimize(tree,Settings(max_outlets=1,population=8,generations=2))
    assert result['solution'] is None and result['stop_reason']=='mandatory_outlet_budget'
    result=optimize(tree,Settings(min_pressure_bar=6.5,population=8,generations=2))
    assert result['solution'] is None


def test_search_evaluation_budget_is_a_hard_ceiling(tree):
    updates=[]
    result=optimize(tree,Settings(evaluation_budget=8,population=64,generations=40),updates.append)
    assert result['evaluated_layouts']<=8
    assert max(update['evaluated'] for update in updates)<=8
    if result['solution']:
        assert result['solution']['feasible']


def test_budget_can_exceed_available_candidates_without_being_rewritten():
    network=ingest_dxf(GRID.read_bytes(),metres_per_unit=1,source_x=110,source_y=10)
    candidates={n.id for n in network.nodes if n.candidate}
    assert len(candidates)==11
    result=optimize(network,Settings(max_outlets=12,source_pressure_bar=7))
    assert result['settings']['max_outlets']==12
    assert result['solution']['feasible']
    assert set(result['solution']['placements']).issubset(candidates)
    assert len(result['solution']['placements'])<=11


@pytest.mark.parametrize('field,values', [
    ('max_outlets',[0,-1,81,1.5]), ('source_pressure_bar',[0,-1,30.1,'nan','inf']),
    ('min_pressure_bar',[0,-1,20.1,'nan','inf']),
    ('pump_capacity_lps',[0,-1,2000.1,'nan','inf']),
    ('outlet_demand_lps',[0,-1,100.1,'nan','inf']),
    ('coverage_radius_m',[0,-1,200.1,'nan','inf']),
    ('diameter_override_mm',[9,2001,'nan','inf']),
])
def test_invalid_setting_values_are_rejected_before_jobs_start(tree,field,values):
    client=TestClient(app)
    for value in values:
        response=client.post('/api/optimize',json={'network':tree.model_dump(),'settings':{field:value}})
        assert response.status_code==422, (field,value,response.text)


@pytest.mark.parametrize('data', [
    {'metres_per_unit':'0'},{'metres_per_unit':'nan'},{'metres_per_unit':'inf'},
    {'source_x':'nan','source_y':'10'},{'source_x':'10'},
    {'source_x':'0','source_y':'0'}, {'diameter_mm':'9'}, {'diameter_mm':'2001'},
])
def test_invalid_cad_settings_fail_cleanly(data):
    response=TestClient(app).post('/api/import',files={'file':('grid.dxf',GRID.read_bytes())},data=data)
    assert response.status_code==422


@pytest.mark.parametrize('scale', [1,.001,.01,.3048])
def test_equivalent_cad_units_produce_the_same_physical_network(scale):
    base=ingest_dxf(GRID.read_bytes(),metres_per_unit=1)
    doc=ezdxf.readfile(GRID)
    for entity in doc.modelspace():
        entity.transform(Matrix44.scale(1/scale))
    stream=io.StringIO();doc.write(stream)
    scaled=ingest_dxf(stream.getvalue().encode(),metres_per_unit=scale)
    assert sum(p.length_m for p in scaled.pipes)==pytest.approx(sum(p.length_m for p in base.pipes),abs=.001)
    assert scaled.import_details.source_coordinates==pytest.approx((10/scale,10/scale))
    assert sum(Coverage(scaled,35).zone_weights.values())==pytest.approx(
        sum(Coverage(base,35).zone_weights.values()),rel=.005)
    settings=Settings(source_pressure_bar=7)
    placements=['N0','N2','N8']
    assert evaluate(scaled,settings,placements)['pressures_bar']==pytest.approx(
        evaluate(base,settings,placements)['pressures_bar'],abs=1e-6)


@pytest.mark.parametrize('overrides', [
    {'max_outlets':1}, {'max_outlets':2}, {'max_outlets':12},
    {'pump_capacity_lps':4.9}, {'pump_capacity_lps':5}, {'pump_capacity_lps':10},
    {'outlet_demand_lps':1}, {'outlet_demand_lps':10}, {'outlet_demand_lps':100},
    {'diameter_override_mm':10}, {'diameter_override_mm':50}, {'diameter_override_mm':2000},
    {'source_pressure_bar':.1}, {'source_pressure_bar':30},
    {'coverage_radius_m':.1}, {'coverage_radius_m':15}, {'coverage_radius_m':200},
    {'max_outlets':12,'outlet_demand_lps':10,'pump_capacity_lps':20,'min_pressure_bar':6.5},
])
def test_industrial_setting_sweep_never_returns_a_constraint_violation(overrides):
    network=ingest_dxf(GRID.read_bytes(),metres_per_unit=1)
    settings=Settings(**({'source_pressure_bar':7}|overrides))
    result=optimize(network,settings)
    assert result['evaluated_layouts']<=settings.evaluation_budget
    solution=result['solution']
    if solution:
        assert 0<len(solution['placements'])<=settings.max_outlets
        assert solution['total_demand_lps']<=settings.pump_capacity_lps+1e-8
        assert min(solution['pressures_bar'][n] for n in solution['placements'])>=settings.min_pressure_bar-1e-6
        assert min(solution['pressures_bar'].values())>=-1e-6
        assert 0<=solution['coverage_pct']<=100
        assert all(math.isfinite(v) for v in solution['pressures_bar'].values())
        assert evaluate(network,settings,solution['placements'])['feasible']
    else:
        assert result['status']=='no_feasible_layout_found'


def test_full_coverage_never_exceeds_one_hundred_percent():
    network=ingest_dxf(GRID.read_bytes(),metres_per_unit=1)
    coverage=Coverage(network,200).calculate(['N0'])
    assert coverage['coverage_pct']==100
    assert all(0<=value<=100 for value in coverage['zones'].values())


@pytest.mark.parametrize('overrides,expected_feasible', [
    ({'source_pressure_bar':30,'min_pressure_bar':20,'diameter_override_mm':2000,
      'outlet_demand_lps':.01,'pump_capacity_lps':2000}, True),
    ({'source_pressure_bar':.1,'min_pressure_bar':20,'diameter_override_mm':10,
      'outlet_demand_lps':100,'pump_capacity_lps':2000}, False),
])
def test_maximum_candidate_network_handles_extreme_inputs_with_bounded_search(overrides,expected_feasible):
    network=Network.model_validate({
        'name':'80-candidate limit', 'source_id':'S',
        'nodes':[{'id':'S','x':-10,'y':0,'candidate':False}]+
                [{'id':f'N{i}','x':(i%10)*10,'y':(i//10)*10} for i in range(80)],
        'pipes':[{'id':'FEED','start':'S','end':'N0','length_m':10}]+
                [{'id':f'E{i}_{j}','start':f'N{i}','end':f'N{j}','length_m':10}
                 for i in range(80) for j in ([i+1] if i%10<9 else [])+([i+10] if i<70 else [])],
        'zones':[{'id':'Z','label':'Entire floor','risk':9,
                  'polygon':[[-5,-5],[95,-5],[95,75],[-5,75]]}]})
    settings=Settings(max_outlets=80,coverage_radius_m=200,evaluation_budget=8,
                      population=64,generations=40,**overrides)
    result=optimize(network,settings)
    assert result['stop_reason']=='evaluation_budget'
    assert result['evaluated_layouts']==8
    assert bool(result['solution'])==expected_feasible
    if expected_feasible:
        assert result['solution']['feasible']
        assert result['solution']['coverage_pct']==100
        assert len(result['solution']['placements'])==1
