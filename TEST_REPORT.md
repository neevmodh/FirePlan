# FirePlan constraint and browser test report

## Latest QA — 21 October 2026

**145 automated tests passed: 129 Python tests and 16 frontend logic tests.**
No application behavior was changed during this QA pass. A reusable frontend
regression suite was added at `tests/test_frontend.mjs`.

| Check | Current result |
| --- | --- |
| Python API, CAD, optimizer and hydraulic regression suite | 129 passed in 33.48 seconds |
| Frontend source-level regression suite | 16 passed; no real browser or server accessed |
| JavaScript syntax | Passed |
| Git whitespace check | Passed |
| Live browser and visual verification | Blocked by the browser tool's URL security policy; not verified in this pass |

```sh
.venv/bin/python -m pytest -q
node --test tests/test_frontend.mjs
node --check web/app.js
git diff --check
```

The Python suite checks the import-to-optimization API workflow, all setup
parameters, exact pressure/capacity boundaries, hydraulic calculations against
an independent reference, polygon-clipped coverage, feasible-layout selection,
bounded searches, malformed CAD, solver failures, concurrency and cancellation.

The frontend suite runs the actual `web/app.js` in an isolated JavaScript VM
with synthetic elements and API responses. It checks initial state, detected
pump coordinates/pressure, preservation of K and manual overrides, clearing
obsolete results when inputs change, CAD rebuilds after geometry settings,
delivered-pressure and threshold messages, coverage rounding, infeasible/failed/
cancelled runs, handling a rejected form submission, and escaping CAD labels
before generating SVG. It does **not** verify native browser validation rules,
rendered layout, accessibility-tree behavior, responsive sizing, or real pointer
and keyboard interactions.

No failures were found in the executed tests. One existing Starlette/httpx
TestClient deprecation warning remains. The running localhost service was not
restarted or modified. The browser observations below are historical results,
not observations from this QA pass.

## Previous verification — 18 October 2026

Updated: 18 October 2026. Environment: local Python 3.11, WNTR 1.5, DEAP, FastAPI, and the Codex in-app browser. Tests use the pinned project environment and the unchanged industrial CAD regression fixture.

## Result

**129 automated tests pass.** JavaScript syntax and Git whitespace checks also passed. One Starlette/httpx deprecation warning remains; it did not affect test execution. The browser was restored to the user's original inputs after testing.

```sh
uv run pytest -q
node --check web/app.js
git diff --check
```

The suite comprises the original 35 checks, 80 constraint/failure cases, and 14 accuracy cases in `tests/test_accuracy.py`.

## Settings and constraints checked

| Control or rule | Evidence | Result |
| --- | --- | --- |
| Maximum outlets K | Industrial K=1, 2, 12; mandatory outlets exceeding K; 80-candidate network; manual supply relocation | Returned layouts respect K and available candidates. User-entered K survives reimport. |
| Source pressure | Analytic head calculation; industrial 0.1–30 bar; browser 1.5 and 6.5 bar; manual overrides across CAD rebuild | Supplied head changes delivered pressure. Insufficient pressure does not yield a feasible result. |
| Minimum pressure | Exact threshold and ±0.0001 bar; industrial 1.5, 6.5, 6.7, 6.9, 7.1 bar; penalty weight=1 | Every active outlet meets the configured threshold within solver tolerance. A soft fitness penalty cannot override hard feasibility. |
| Supply capacity | Exact total demand and ±0.0001 L/s; background demand; industrial capacities 4.9, 5, 10 L/s | Includes all simultaneous outlet and background demands; insufficient capacity is rejected. |
| Outlet demand | Independent calculations at 1, 5, 20 L/s; industrial 1, 10, 100 L/s; browser 5 versus 10 L/s | Demand changes flow, nonlinear losses, and the feasible count. |
| Coverage radius | Industrial 0.1, 15, 200 m; browser 20 versus 200 m; overlapping outlets | Changes geometric coverage without changing prescribed demand. Overlapping covered area is counted once per zone. |
| Pipe diameter | Independent calculations at 50, 100, 200 mm; industrial 10, 50, 2000 mm; browser 50 versus 120 mm | Smaller diameter increases losses and can reduce feasible placements. |
| Drawing units | Equivalent CAD geometry expressed in metres, millimetres, centimetres, and feet; backend and browser | Correct conversions preserve physical lengths, risk areas, pressures, and coverage. Source X/Y use raw drawing coordinates. |
| Supply X and Y | Browser positions (10,10), (110,10), (110,70); off-pipe points; partial/nonfinite coordinates | Valid manual supply points rebuild the hydraulic graph. Invalid points fail validation and clear the old overlay. |
| Hydraulic equations | Nine demand/diameter cases against a separate closed-form tree calculation; existing loop continuity and reversed-flow checks | WNTR matches independent pressure results within 0.002 bar and pipe flows within 0.000001 L/s. |
| GA placement quality | Enumerate all seven nonempty layouts on a three-candidate reference network across ten scenarios | GA matches the exhaustive best feasible score and outlet-count tie break on this reference problem. |
| Search bounds | Unique solve budget=8; 80 candidates; population=64 and generations=40 | The solve ceiling holds; both extreme feasible and infeasible cases complete with the expected outcome. |
| Failure handling | Invalid ranges/nonfinite settings; ten malformed or ambiguous CAD scenarios; four corrupt/binary inputs; solver exceptions and NaN results | Validation errors remain controlled; invalid hydraulic results never pass as feasible layouts. |
| Jobs | Pause one job, attempt a second request, cancel, and then run a real optimization | Second concurrent request returns 429; cancellation completes; the next real optimization succeeds. |

Numerical acceptance uses 0.000001 bar for the summed active-pressure deficit and junction nonnegativity, and 0.00000001 L/s for capacity. Boundary tests distinguish numerical tolerance from a meaningful violation. The zero-budget case is also blocked by the browser's native form validation.

## Historical browser results — 13 October 2026

These observations used the former sampled-coverage model. Current coverage and placement results below supersede these percentages.

The starting setup was K=12, source=5 bar, minimum=3.5 bar, capacity=40 L/s, demand=10 L/s, radius=20 m, diameter=120 mm, metre coordinates, supply=(10,10). Each row below identifies the change from that setup unless otherwise stated.

| Browser case | Observed result |
| --- | --- |
| Original/restored setup | 4 outlets, 49.4% risk coverage, lowest pressure 4.83 bar, margin +1.331 bar |
| K=1 | 1 outlet |
| Capacity=10 L/s, demand=10 L/s | 1 outlet |
| Demand=5 L/s, capacity=40 L/s | 8 outlets |
| Radius=200 m, demand=5 L/s | 1 outlet, 100% modeled risk coverage |
| Diameter=50 mm | 1 outlet, lowest pressure 4.32 bar |
| Supply=(110,10) | Lowest pressure 4.71 bar; 11 eligible candidates; K remains 12 after the fix |
| Supply=(110,70) | Lowest pressure 4.70 bar |
| Equivalent metre/mm/cm/foot drawings | All return 4 outlets, 49.4% coverage, lowest pressure 4.83 bar |
| Minimum=4.9 bar, source=5 bar | 3 outlets, lowest pressure 4.91 bar |
| Minimum=4.9 bar, source=1.5 bar | No feasible layout found; previous measurements and selections cleared |
| Minimum=4.9 bar, source=6.5 bar | 4 outlets, lowest pressure 6.33 bar |
| K=0 | Native form validation rejects the value; optimization does not start |
| Supply point off the pipe network | Import validation reports the 1 cm connection requirement |

## Corrections made

1. **Coverage rounding:** full coverage previously serialized as `100.00000000000092%`. Total and per-zone coverage are now bounded to 0–100. This earlier correction bounded display output; the 18 October upgrade also replaces the underlying coverage calculation.
2. **Budget preservation:** CAD reimport previously changed K to the number of available candidates. Moving the supply onto a candidate could silently change K=12 to K=11. K now stays at the user-entered ceiling, while the optimizer independently limits selection to eligible CAD candidates.

## Limits of this result

These checks validate the implemented steady-state, fixed-demand, constant-head and polygon-clipped radius model. Exhaustive optimality was checked only on the small reference network; the industrial and 80-candidate searches remain bounded GA searches. Drawing annotations are inputs rather than measured supply data, and coverage ignores walls/nozzle behavior. Passing software tests does not establish regulatory approval of an installation.


## Accuracy upgrade — 18 October 2026

- Replaced 12×12 midpoint sampling with risk-weighted unions of inscribed 512-sided outlet disks intersected with CAD zone polygons. Full-, half-, quarter-circle and overlapping-lens area tests use independent formulas and 0.003–0.004% relative tolerances.
- Tested tiny circles, thin concave regions, duplicate outlets, disjoint coverage, and overlapping additive risk zones.
- Added an independent signed-flow balance check at every junction and at the source. A finite result with missing nodes/pipes or a flow mismatch above 0.00001 L/s is rejected.
- Reserved up to 32 of the existing unique search evaluations for outlet removals, additions and swaps. Equal coverage and count prefer more delivered pressure. Total hydraulic search evaluations remain bounded by the configured ceiling.
- Added explicit tests that one bounded outlet swap improves a poor feasible incumbent and that equal-coverage alternatives prefer higher pressure.

### Current industrial results

At source=7 bar, K=8, demand=5 L/s, capacity=60 L/s, radius=35 m and diameter=100 mm:

| Minimum pressure | Outlets | Risk-weighted area covered | Lowest pressure |
| --- | ---: | ---: | ---: |
| 1.5 or 3.5 or 6.5 bar | 7 | 100% | 6.69116 bar |
| 6.7 bar | 6 | 99.62040% | 6.75283 bar |
| 6.9 bar | 4 | 87.07858% | 6.91233 bar |
| 7.1 bar | No feasible layout found | — | — |

The former seven-outlet layout (`N0,N2,N3,N4,N8,N10,N6`) is 99.88373% covered under the new area calculation. The new seven-outlet layout (`N0,N2,N1,N7,N5,N10,N6`) covers the complete modeled zone area. This corrects the old grid's false full-coverage result.

With identical GA initialization and geometry at a 6.9 bar threshold, disabling the final refinement stage gives 82.96132% coverage and 186 unique hydraulic evaluations. Enabling it gives 87.07858%, four outlets, 199 total unique evaluations, and a positive pressure margin. This controlled comparison tests the observed benefit on this fixture; it does not prove global optimality.

For the user's prior setup (K=12, source=5 bar, minimum=3.5 bar, capacity=40 L/s, demand=10 L/s, radius=20 m, diameter=120 mm), the new solution uses `N2,N1,N8,N5`, covers 52.77869% of risk-weighted area, uses 40 L/s, and retains at least 4.83095 bar.

Current browser verification reproduced 52.78% coverage, four outlets, 4.83 bar and +1.331 bar margin for that setup. At radius=0.1 m, coverage is correctly displayed as `<0.01%`. A 5.1 bar minimum with a 5 bar source returns no feasible layout and clears the previous result. At a 390 px mobile viewport, the document width remains 390 px without horizontal overflow. The normal viewport and the user's original inputs were restored afterward.
