"""Analytic geometry, numerical integrity and bounded layout refinement."""
import math
from types import SimpleNamespace

import pandas as pd
import pytest
import wntr

from fireplan.hydraulics import Coverage, evaluate
from fireplan.models import Network, Settings
from fireplan.optimizer import Search, refine_layout


def network(polygon, candidates=((0,0),)):
    return Network.model_validate({
        'name':'Analytic geometry', 'source_id':'S',
        'nodes':[{'id':'S','x':-5,'y':-5,'candidate':False}] +
                [{'id':f'N{i}','x':x,'y':y} for i,(x,y) in enumerate(candidates)],
        'pipes':[{'id':f'P{i}','start':'S','end':f'N{i}','length_m':20} for i in range(len(candidates))],
        'zones':[{'id':'Z','label':'Reference zone','risk':2,'polygon':polygon}]})


@pytest.mark.parametrize('polygon,area', [
    ([[-2,-2],[2,-2],[2,2],[-2,2]],math.pi),
    ([[0,-2],[2,-2],[2,2],[0,2]],math.pi/2),
    ([[0,0],[2,0],[2,2],[0,2]],math.pi/4),
])
def test_clipped_circle_area_matches_analytic_full_half_and_quarter(polygon,area):
    result=Coverage(network(polygon),1).calculate(['N0'])
    assert result['covered_area_m2']['Z']==pytest.approx(area,rel=3e-5)
    assert result['weighted_score']==pytest.approx(2*area,rel=3e-5)


def test_overlapping_circles_match_analytic_lens_union():
    n=network([[-3,-3],[3,-3],[3,3],[-3,3]],[(-.5,0),(.5,0)])
    overlap=2*math.acos(.5)-.5*math.sqrt(3)
    expected=2*math.pi-overlap
    result=Coverage(n,1).calculate(['N0','N1'])
    assert result['covered_area_m2']['Z']==pytest.approx(expected,rel=4e-5)


def test_tiny_circle_cannot_receive_the_area_of_a_whole_sample_cell():
    n=network([[-50,-50],[50,-50],[50,50],[-50,50]],[(0,0)])
    result=Coverage(n,.1).calculate(['N0'])
    assert 0<result['covered_area_m2']['Z']<=math.pi*.1**2
    assert result['covered_area_m2']['Z']==pytest.approx(math.pi*.1**2,rel=3e-5)


def test_thin_concave_zone_uses_its_actual_area():
    polygon=[[0,0],[10,0],[10,.01],[.01,.01],[.01,10],[0,10]]
    result=Coverage(network(polygon),20).calculate(['N0'])
    assert result['covered_area_m2']['Z']==pytest.approx(.1999)
    assert result['coverage_pct']==100


def test_disjoint_outside_and_duplicate_placements_do_not_inflate_area():
    n=network([[-2,-2],[2,-2],[2,2],[-2,2]],[(0,0),(20,20)])
    cov=Coverage(n,1)
    assert cov.calculate(['N1'])['coverage_pct']==0
    assert cov.calculate(['N0'])==cov.calculate(['N0','N0'])
    assert cov.calculate(['N0'])==cov.calculate(['N1','N0'])
    assert cov.calculate([])['coverage_pct']==0


def test_overlapping_risk_layers_keep_their_separate_priorities():
    n=network([[-2,-2],[2,-2],[2,2],[-2,2]])
    data=n.model_dump()
    data['zones'].append({**data['zones'][0],'id':'Z2','risk':5})
    result=Coverage(Network.model_validate(data),1).calculate(['N0'])
    assert result['weighted_score']==pytest.approx(7*math.pi,rel=3e-5)
    assert result['zones']['Z']==pytest.approx(result['zones']['Z2'])


@pytest.mark.parametrize('corruption',['missing_pipe','missing_node','unbalanced_flow'])
def test_finite_but_inconsistent_solver_outputs_are_rejected(monkeypatch,corruption):
    n=network([[-2,-2],[2,-2],[2,2],[-2,2]])
    pressure={'S':[0.], 'N0':[50.]}
    flows={'P0':[.005]}
    if corruption=='missing_pipe':flows={}
    elif corruption=='missing_node':pressure={'S':[0.]}
    else:flows={'P0':[.001]}
    fake=SimpleNamespace(node={'pressure':pd.DataFrame(pressure)},link={'flowrate':pd.DataFrame(flows)})
    monkeypatch.setattr(wntr.sim.WNTRSimulator,'run_sim',lambda *a,**kw:fake)
    result=evaluate(n,Settings(),['N0'])
    assert not result['feasible']
    assert 'hydraulic_solver_failure' in result['violations']


def test_real_solver_reports_independent_continuity_residuals():
    n=network([[-2,-2],[2,-2],[2,2],[-2,2]])
    result=evaluate(n,Settings(),['N0'])
    assert result['feasible']
    assert result['hydraulic_validation']['max_junction_flow_error_lps']<1e-5
    assert result['hydraulic_validation']['source_flow_error_lps']<1e-5


def test_bounded_local_swap_can_improve_a_feasible_ga_incumbent():
    n=network([[-1,-1],[1,-1],[1,1],[-1,1]],[(2,0),(0,0)])
    settings=Settings(max_outlets=1,coverage_radius_m=1,source_pressure_bar=7)
    search=Search(n,settings,None)
    search.measure([1,0])
    before=search.best['weighted_score']
    count=refine_layout(search,1)
    assert count==1
    assert search.best['placements']==['N1']
    assert search.best['weighted_score']>before
    assert search.best['feasible']


def test_equal_coverage_and_count_prefer_greater_pressure_margin():
    n=network([[-1,-1],[1,-1],[1,1],[-1,1]],[(0,0),(.1,0)])
    n.pipes[0].length_m=100
    n.pipes[1].length_m=10
    search=Search(n,Settings(max_outlets=1,coverage_radius_m=10),None)
    search.measure([1,0]);first=search.best
    search.measure([0,1])
    assert search.best['weighted_score']==first['weighted_score']
    assert search.best['min_active_pressure_bar']>first['min_active_pressure_bar']
    assert search.best['placements']==['N1']
