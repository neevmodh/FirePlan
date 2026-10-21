# FirePlan

A simple **one-page CAD optimizer**: import a DXF drawing, configure one setup panel, and view the best feasible sprinkler/hydrant layout in the plan workspace.

The website contains only **CAD import**, **Optimal setup**, and **Optimal plan workspace**. It starts empty, so your imported drawing is the project. Local archives, generated screenshots, caches, and virtual environments are excluded from this repository.

## Run

Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/) are required.

```sh
git clone https://github.com/neevmodh/FirePlan.git
cd FirePlan
uv sync --locked --python 3.11
uv run uvicorn fireplan.app:app --host 127.0.0.1 --port 8000
```

Open [FirePlan](http://127.0.0.1:8000/). There is no frontend build step or API key requirement.

For a ready-to-import example, select [the industrial grid DXF](tests/fixtures/industrial-grid.dxf) with **Metres** selected. The drawing supplies the marked pump at (10, 10), its annotated 7 bar source pressure, 12 candidate locations, and four risk regions. The smaller [office DXF](tests/fixtures/office.dxf) is also included.

## Use

1. Set drawing units in **Optimal setup**. A labeled pump is detected automatically; edit supply X/Y to override it.
2. Click **Import CAD** and select an ASCII `.dxf` file.
3. Set outlet count, water inputs, coverage radius, and pipe diameter in the same panel.
4. Click **Optimize plan**. Selected outlets appear on the imported CAD, with risk coverage, outlet count, and lowest delivered outlet pressure beneath the drawing. The pressure check also shows your required threshold and remaining margin.

Changing settings clears the previous result. Changing drawing units, supply coordinates, or diameter causes the CAD to be rebuilt on the next optimization. The same action button can cancel a running search.

## Understanding Optimal setup

This panel defines the conditions under which the optimizer must find a layout. The drawing supplies the pipe routes, possible outlet locations, and risk areas. The setup supplies the water limits and coverage assumptions. Every layout in a search is checked against the same setup.

### What the headings mean

- **01:** a visual step number for the configuration panel. It is not the outlet count, a solver iteration, or an input value.
- **CONFIGURE ONCE:** enter one shared setup for the current optimization. You can change it and run again; it does not mean the settings are permanently saved.
- **Optimal setup:** the panel containing the CAD and engineering inputs.
- **CAD and water settings:** the form's accessibility/group label, not another setting to fill in.

The page stores its drawing and result in browser memory. Reloading starts a fresh session with default controls; importing a labeled pump can then populate source coordinates and pressure again.

### Every field explained

| Field | What it means | How it affects the result |
| --- | --- | --- |
| **Maximum outlets K** | The maximum number of selected sprinklers/hydrants. It is a count limit, not a money amount. | With K=8, a returned layout may contain 1–8 outlets. Increasing K gives the search more placement options, but water constraints may still prevent using all slots. K remains your entered ceiling when CAD or source coordinates change; fewer available candidates naturally limit the selection. Equal-coverage layouts prefer fewer outlets. |
| **Source pressure — bar** | Pressure available where the water enters the modeled network, before downstream pipe losses. | Higher source pressure provides more pressure head in the simulation. Your DXF declares 7 bar at its pump; that annotation fills the input unless you manually override it. It is drawing data, not a measured supply test. |
| **Minimum pressure — bar** | The lowest allowed pressure at every selected outlet while all selected outlets operate. | An outlet below the threshold makes the layout infeasible. With 3.5 bar entered, every selected outlet must retain at least 3.5 bar. A stricter threshold can reduce the feasible choices. |
| **Supply capacity — L/s** | Maximum total water flow the source is allowed to supply simultaneously. | This is checked separately from pressure. With 60 L/s capacity, selected outlet demand plus any background demand must not exceed 60 L/s. A source can have sufficient pressure but insufficient flow capacity. |
| **Outlet demand — L/s** | Fixed water flow required by each active outlet. The current model uses the same demand for every selected outlet. | Seven outlets at 5 L/s require 35 L/s before background demand. Increasing demand raises total flow and generally increases friction losses, making some layouts infeasible. Demand is not automatically reduced when pressure is low. |
| **Coverage radius — m** | The modeled protection distance around a selected outlet. | The app draws a circle of this radius and calculates its union with other selected circles, clipped to the actual risk polygons. A larger radius can protect more modeled area with fewer outlets. Coverage circles ignore walls and hose paths; this setting does not alter water demand directly. |
| **Pipe diameter — mm** | The effective hydraulic diameter used for every pipe in this scenario. | Wider pipes generally lose less pressure at the same flow. The current single-page setup applies one diameter to all pipes; it does not infer different sizes from the drawing or optimize pipe upgrades. |
| **Drawing units** | The physical length represented by one coordinate unit in the DXF. | This converts coordinates and pipe lengths into metres before simulation. A wrong selection changes the modeled building size, coverage distances, and friction losses. Choose the drawing's actual units. |
| **Supply X** | Horizontal CAD coordinate of the source connection. | It locates where water enters the graph. It is a position, not a pressure or flow value. An automatic pump marker fills it; editing it requests a manual override. |
| **Supply Y** | Vertical CAD coordinate of the same source connection. | Together with X, it must identify a point on a pipe within the import tolerance. Your file's source is (10, 10) in metre drawing units. These are local CAD coordinates, not GPS coordinates. |

### Units in plain language

| Displayed unit | Meaning | Example |
| --- | --- | --- |
| **outlets** | Number of selected devices | K=8 permits at most eight devices |
| **bar** | Pressure | 1 bar is approximately 10.1972 metres of water head in this model |
| **L/s** | Litres per second, a flow rate | 5 L/s per outlet × 7 outlets = 35 L/s |
| **m** | Metres of physical distance | Radius 35 m means points within 35 metres may receive coverage credit |
| **mm** | Millimetres of pipe diameter | 100 mm = 0.1 m inside the hydraulic solver |

Drawing-unit conversion is separate from these field units:

| Drawing selection | Metres per CAD coordinate unit |
| --- | --- |
| Metres | 1 |
| Millimetres | 0.001 |
| Centimetres | 0.01 |
| Feet | 0.3048 |

For example, a line 10,000 units long in a millimetre drawing becomes 10 m. Supply coordinates must still be entered in the original drawing units: a source at X=2,000 mm is entered as `2000`, and the importer converts it to 2 m. Your industrial grid declares metre units, so select **Metres**.

### Automatic pump detection and the CAD-layer hint

The text **“Marked pumps are detected automatically; edit X/Y to override”** means the importer looks for a pipe-layer circle next to a recognized `MAIN_PUMP_SOURCE` label. Its center supplies X/Y. A declared `P_in` value in bar can also fill source pressure. Manually edited source coordinates and pressure take precedence over their automatic values.

If no recognized pump exists, the source falls back to (0, 0) until you set a different location. A manual source may lie inside a pipe segment; the importer creates the required junction there. Moving the source changes the hydraulic paths, so the plan is rebuilt and solved again.

The layer hint describes how the app distinguishes drawing content:

- **`PIPES` / `WATER_PIPES_MAIN` / `WATER_PIPES_BRANCH`:** explicit water routes, converted into hydraulic edges. The corresponding `PIPES_MAIN` and `PIPES_BRANCH` names are also supported.
- **Risk polygons or labeled rooms:** areas to protect and their relative priorities. Explicit `RISK_n` polygons are read directly; `ZONE_n: NAME (RISK_WEIGHT=value)` labels are matched to enclosed structural rooms.
- **`CANDIDATE_NODES`:** optional circle markers identifying eligible outlet positions. Your file provides 12 candidates named N0–N11. Other pipe junctions remain hydraulic connections rather than extra placement candidates when this layer exists.
- **Structural walls:** visible plan context and, when using room labels, risk boundaries. Walls are never treated as water pipes or as barriers to the current radius-coverage model.
- **Convert DWG to DXF first:** the importer reads ASCII DXF. Renaming a `.dwg` file to `.dxf` does not convert it.

### What happens when you click Optimize plan

1. The app checks the form and, if needed, rebuilds the CAD using the updated units, source location, or diameter.
2. **Genetic Algorithms, Unit VI:** each candidate has a binary selected/not-selected value. The algorithm evolves different combinations using selection, crossover, and mutation.
3. Each new combination gets a coverage calculation and a WNTR solve for pipe flows and node pressures at simultaneous outlet demand.
4. **Constrained Optimization / Penalty Methods, Unit IV:** normalized quadratic penalties discourage violations during search. A separate record retains the best nonempty layout that passes every hard constraint.
5. The chosen feasible layout gets a fresh hydraulic verification. Selected outlets show their calculated pressures on the drawing. Coverage, outlet count, lowest delivered pressure, required pressure, and pressure margin appear below the plan.

The search uses the fixed controls documented below: population 28, up to 16 generations, and at most 250 unique search evaluations. It may finish earlier. Repeated cached layouts do not consume another evaluation, and final verification is an additional solve. While a job runs, the action button becomes **Cancel optimization**.

### What the success message means

**“Plan optimized. All water and budget limits pass.”** means a nonempty returned layout passed the checks for your current inputs:

- Selected outlet count is at most K.
- Total simultaneous demand is within supply capacity.
- Every selected outlet reaches minimum pressure.
- All junction pressures are nonnegative and the hydraulic solve is valid and finite.
- Any required outlets supplied in the network remain selected.

It does not mean every budget slot was used, a global optimum was mathematically proven, or the actual installation was certified. If the bounded search finds no feasible layout, the app reports that instead of presenting an infeasible attempt as an optimal plan.

### Why 1.5 bar and 6.5 bar can produce the same layout

**Source pressure** supplies hydraulic head. **Minimum pressure** is an acceptance threshold: every selected outlet must meet it. Changing only the threshold does not physically change pressures in a fixed layout. The optimizer must change the placement if the old layout fails the new threshold and it finds a feasible alternative.

With your industrial drawing, the imported pump pressure is 7 bar. At a fixed 35 m coverage radius, K=8, capacity=60 L/s, demand=5 L/s, and diameter=100 mm, the default seeded GA produces:

| Required minimum | Selected outlets | Lowest delivered pressure | Pressure margin | Risk coverage |
| --- | ---: | ---: | ---: | ---: |
| 1.5 bar | 7 | 6.691 bar | +5.191 bar | 100% |
| 6.5 bar | 7, same positions | 6.691 bar | +0.191 bar | 100% |
| 6.7 bar | 6 | 6.753 bar | +0.053 bar | 99.62% |
| 6.9 bar | 4 | 6.912 bar | +0.012 bar | 87.08% |
| 7.1 bar | No feasible layout found | — | — | — |

These are observed results for this drawing and the documented search defaults, not guaranteed global optima for every plan. The first two layouts match because 6.691 bar already satisfies both thresholds. The margin changes by exactly 5 bar. The former seven-outlet layout fails the 6.7 bar check, and the GA selects fewer outlets to reduce simultaneous demand and friction loss.

The website now distinguishes **Lowest outlet pressure** from the **Minimum pressure** input. It shows `Required … bar · margin +… bar`, explains an unchanged layout after a minimum-only edit, and highlights outlets within 0.05 bar of the required threshold in amber. Editing inputs clears the previous overlay and measurements; click **Optimize plan** to compute the new result. Unchanged placement does not mean an old result was reused: each optimization starts a new search and final hydraulic verification.

To change the pressure actually supplied to the network, edit **Source pressure**. For the same active outlets in this fixed-demand, constant-head model, increasing source pressure from 1.5 to 6.5 bar increases delivered node pressures by 5 bar while pipe flows remain the same. Whether that layout passes the minimum-pressure check can change. Coverage radius remains the entered geometric distance: this prototype does not model nozzle discharge or derive spray radius from pressure.

### Worked example using your industrial grid

The tested setup uses K=8, source pressure 7 bar, minimum pressure 3.5 bar, capacity 60 L/s, outlet demand 5 L/s, radius 35 m, diameter 100 mm, metre drawing units, and source (10, 10).

Before friction is considered, the count and capacity bounds give:

```text
capacity-only outlet bound = floor(60 / 5) = 12
count bound                = K = 8
available CAD candidates   = 12
permitted count ceiling    = min(12, 8, 12) = 8
```

This example has no background demand. For a network with background demand, subtract that demand from capacity before dividing by outlet demand. These count bounds do not replace the hydraulic pressure check.

The tested search returned seven outlets:

| Check | Calculation / result |
| --- | --- |
| Count | 7 ≤ 8; one unused slot is allowed |
| Flow | 7 × 5 = 35 L/s ≤ 60 L/s; 25 L/s capacity margin |
| Pressure | Minimum active pressure approximately 6.69 bar ≥ 3.5 bar; approximately 3.19 bar pressure margin |
| Coverage | 100% of the modeled risk-weighted polygon area is covered |

Seven is allowed because K is a maximum, and equal-coverage results prefer fewer outlets. This observed layout is not proof that seven is the minimum possible number. The 100% figure applies to the declared risk polygons and radius assumptions; it is not a probability of preventing every fire.

## CAD requirements

| Input | Convention |
| --- | --- |
| File | ASCII DXF, up to 4 MiB; convert DWG to DXF first |
| Pipes | Straight `LINE` / `LWPOLYLINE` on `PIPES`, `PIPES_MAIN`, `PIPES_BRANCH`, `WATER_PIPES_MAIN`, or `WATER_PIPES_BRANCH` |
| Risk zones | Closed straight `LWPOLYLINE` on `RISK_0`–`RISK_10`; alternatively, exact `ZONE_n: NAME (RISK_WEIGHT=value)` labels on `HAZARD_LABELS` inside enclosed `STRUCTURAL_WALLS` / `WALLS` rooms |
| Units | Metres, millimetres, centimetres, or feet, selected before import |
| Source | A pipe-layer circle beside `MAIN_PUMP_SOURCE (P_in = value bar)` is detected automatically; manual X/Y override must lie on a pipe within 1 cm after scaling |
| Elevation | Planar z=0 CAD |
| Architecture | Straight planar lines/polylines from other layers are retained as visual context |

Same-level pipe intersections become junctions. Source points and explicit `CANDIDATE_NODES` circle centers also split pipe segments. When this candidate layer exists, only its circles are eligible placements, with nearby node labels retained; otherwise all nonsource pipe junctions are candidates. Coordinates snap to 0.1 mm. Every pipe node must connect to the source. Structural context is drawn separately and never creates a hydraulic connection. Risk-label import requires exactly one label in every enclosed room; boundaries and weights are not guessed for missing labels.

The importer handles Unix and Windows line endings. Unsupported curved/elevated pipes and blocks on the pipe layer are rejected. Arcs, text, blocks, and curved structural entities are not rendered. A plain architectural drawing still needs explicit pipes and risk data.

Detected pump coordinates and a declared `P_in` pressure fill the existing setup inputs, unless manually overridden. Drawing annotations are supplied design inputs, not verified supply measurements. `Industrial_Fire_Optimization_Grid.dxf` is covered by an unchanged regression fixture: 12 declared candidate outlets, 15 pipes, four risk regions, and source (10, 10) with an annotated 7 bar pressure.

Limits: 120 nodes, 80 candidates, 250 pipes, 40 zones, 100 polygon vertices, 1000 raw pipe segments, and 2000 structural context segments. Imported pipes initially use the setup diameter, C=120, and zero minor loss.

## Syllabus topics

| Topic | Unit | Implemented use |
| --- | --- | --- |
| **Genetic Algorithms** | **VI: Metaheuristic and Evolutionary Optimization** | Binary outlet placements, tournament selection, uniform crossover, mutation, elitism and random immigrants |
| **Constrained Optimization / Penalty Methods** | **IV: Constrained Optimization** | Normalized quadratic exterior penalties and independent hard feasibility checks |

Objective functions, feasible regions, and nonlinear/combinatorial formulation from Unit I provide the background. The app uses one GA configuration; there is no solver selector or comparison screen. Lagrange multipliers and KKT certificates are not computed.

## Architecture and stack

```text
CAD DXF → ezdxf + Shapely → validated NetworkX pipe graph
        → GA binary placement search (DEAP)
        → coverage + WNTR hydraulic simulation for each layout
        → penalties + bounded outlet refinement + verified feasible incumbent
        → one SVG plan workspace
```

FastAPI and Uvicorn serve the local app. Pydantic validates inputs. Plain HTML/CSS/JavaScript renders the page. WNTR solves Hazen–Williams flow and head equations; NetworkX checks connectivity. Exact dependency versions are in `uv.lock`.

| Module | Role |
| --- | --- |
| `fireplan/ingestion.py` | DXF pipe/risk ingestion and straight CAD context |
| `fireplan/models.py` | Numeric, graph, geometry and request validation |
| `fireplan/hydraulics.py` | Coverage, WNTR simulation and constraints |
| `fireplan/optimizer.py` | GA, bounded outlet swaps/removals/additions, quadratic penalties and final verification |
| `fireplan/app.py` | Import and asynchronous optimization APIs |
| `web/index.html`, `web/style.css`, `web/app.js` | Single-page interface |

## Setup parameters

| Visible parameter | Default | API range / meaning |
| --- | --- | --- |
| Maximum outlets K | 8 | Integer 1–80; a count budget |
| Source pressure | 6 bar | >0 to 30; constant-head source boundary |
| Minimum active pressure | 3.5 bar | >0 to 20; configurable design threshold |
| Supply capacity | 60 L/s | >0 to 2000; simultaneous total-demand ceiling |
| Demand per outlet | 5 L/s | >0 to 100; all selected outlets operate together |
| Coverage radius | 35 m | >0 to 200; Euclidean coverage proxy |
| Pipe diameter | 100 mm | 10–2000; uniform for the imported scenario |
| Drawing units | Metres | Scale 1, 0.001, 0.01, or 0.3048 m per CAD unit |
| Supply X/Y | Detected pump, or 0 / 0 fallback | Finite original CAD coordinates; manual values override detection |

Fixed search defaults: GA population 28, generations 16, unique evaluation ceiling 250, λ=500, seed 42. The GA uses tournament size 3, crossover probability 0.8 with per-bit probability 0.5, mutation probability 0.6 with bit probability `max(1/N, 0.04)`, two elites, and one random immigrant per generation. For budgets of at least 64, up to `min(32, floor(budget/5))` solves are reserved for bounded outlet refinement; the GA uses the remainder. The total unique-solve ceiling still applies to both stages together. At most 128 neighboring layouts are considered, and only candidates that can improve the retained objective need a hydraulic solve. Iteration limits can stop search before its ceiling. Final verification is an additional solve.

## Objective and constraints

### Requested engineering constraints

The Genetic Algorithm evaluates these constraints for every new placement and repeats the hydraulic check before returning a feasible result. They are active engine behavior, not only equations shown in this document.

**1. Outlet-count budget**

Let `y_n=1` when a candidate has an active outlet, otherwise `y_n=0`. Noncandidate nodes have `y_n=0`.

$$
\sum_{n\in V} y_n \le K, \qquad y_n\in\{0,1\}.
$$

The GA repairs chromosomes exceeding K by removing optional outlets. The hydraulic evaluator also checks the count independently. Required outlets cannot be removed to satisfy the budget. K limits the number of outlets; actual source flow is a separate constraint:

$$
\sum_{n\ne s}(b_n+d\,y_n)\le Q_{\max}.
$$

Here `b_n` is background demand, `d` is demand per active outlet, and `s` is the source. Use consistent flow units. For example, K=8 and demand=5 L/s allow at most eight outlets, while their combined demand must also fit the supply-capacity input.

**2. Nonlinear hydraulic friction: Hazen–Williams**

The standard SI coefficient gives hydraulic **head loss**, so the implementation writes the energy equation using `H`, measured in metres:

$$
H_i-H_j=\frac{10.667\,L_{ij}\,Q_{ij}|Q_{ij}|^{0.852}}
{C_{ij}^{1.852}\,D_{ij}^{4.871}}.
$$

`L` and `D` are in metres, `Q` is in m³/s, and `C` is the dimensionless Hazen–Williams coefficient. The signed flow term allows reverse flow through loops. For positive flow, it reduces to `Q^1.852`. WNTR solves these equations together with junction flow conservation and applies its numerical formulation near zero flow. Imported pipes have zero minor loss; any supplied nonzero minor-loss coefficient is also passed to WNTR.

The expression in the project brief cannot be used directly as a pressure difference in bar without conversion. With `alpha = 10.1972 m/bar`:

$$
P_n=\frac{H_n-e_n}{\alpha},\qquad
P_i-P_j=\frac{(H_i-H_j)-(e_i-e_j)}{\alpha}.
$$

Here `e_n` denotes node elevation in metres. At equal elevations, pressure drop in bar is head loss divided by alpha. Increasing flow increases friction nonlinearly; increasing diameter reduces it. Pipe pressures are computed by WNTR for each placement, not estimated only from distance.

**3. Minimum operational pressure**

$$
P_n\ge P_{\min}\,y_n\quad\forall n\in V.
$$

Every selected outlet must retain at least the configured minimum pressure at its full simultaneous demand. All junctions must also have nonnegative pressure. The app uses 3.5 bar as its default design input; an applicable statutory threshold must be supplied for the actual facility, since this prototype has no regulatory rule engine. A pressure-violating layout cannot be returned as feasible even if it covers more risk area.

### Genetic Algorithm with constrained fitness

The single-page optimizer uses **Genetic Algorithms from Unit VI** and **quadratic penalty methods from Unit IV**:

1. Encode one bit per candidate: `1` selects the outlet and `0` leaves it inactive.
2. Initialize a population using random, coverage-greedy, existing and single-outlet layouts.
3. Calculate risk coverage and solve WNTR hydraulics for each new chromosome.
4. Maximize `coverage_pct - lambda × sum(squared normalized positive violations)`. Pressure, flow-capacity, budget, nonnegative-pressure and required-outlet violations contribute penalties. Failed hydraulic solves receive an unusable score.
5. Use tournament selection, uniform crossover, bit mutation, two elites and a random immigrant to form subsequent generations. Repair count and required-outlet constraints.
6. Keep a separate best feasible incumbent. Use the reserved solves to check outlet removals, additions, and swaps. Equal coverage prefers fewer outlets; equal coverage and count prefer higher minimum outlet pressure.
7. Solve the retained layout again and independently check every junction’s flow balance and total source flow. Return it only if every hard check passes.

Defaults are population 28, up to 16 generations, 250 unique search evaluations, lambda=500, and seed=42. Penalties guide exploration; they do not replace hard feasibility. The reported layout is the best feasible solution found within the search limits, not a certified global optimum.

### Complete placement model

For candidate n, `y_n` is binary. Each selected node defines a radius-r disk. The objective sums the area of each risk zone intersected by the union of selected disks, multiplied by that zone’s risk priority.

```text
maximize       Σ_z R_z × area(zone_z ∩ union of selected outlet disks)
subject to     Σ_n y_n ≤ K
               required outlets remain selected, if supplied in a Network
               junction inflow − outflow = background demand + d × y_n
               total simultaneous demand ≤ supply capacity
               active-outlet pressure ≥ minimum pressure
               every junction pressure ≥ 0
               valid finite hydraulic solution
```

Coverage uses Shapely intersections and unions on the actual CAD polygons. Each disk is an inscribed 512-sided polygon (`quad_segs=128`); its full-circle area underestimation is below 0.00251%. This is a bound for a full disk, not a universal relative-error bound for every clipped sliver. Overlapping outlets count once within each zone; overlapping risk polygons remain additive layers. Thin and concave rooms no longer depend on grid sample locations. Risk values are explicit priorities from 0 to 10, not calibrated probabilities.

The former 12×12 grid reported the old seven-outlet industrial layout as 100% covered; its polygon-area coverage is actually 99.88373%. The updated search selects different positions to achieve full modeled coverage. At a 6.9 bar minimum, bounded refinement improves the same GA result from 82.96132% to 87.07858% while retaining four outlets and satisfying pressure. These are observed results for the documented seeded example, not a general optimality guarantee.

For a pipe with signed flow Q:

```text
H_i − H_j = 10.667 L Q |Q|^0.852 / (C^1.852 D^4.871)
P_bar = (H − elevation) / 10.1972
```

Head, length, diameter and elevation use metres internally; Q uses m³/s. API demands use L/s. WNTR applies its numerical formulation near zero flow. Pressure thresholds use bar. The source is a reservoir boundary with a separate flow ceiling, not a pump curve.

Fitness is coverage percentage minus λ times the sum of squared normalized positive violations: active pressure deficit, capacity excess, count excess, negative junction pressure, and missing required outlets. Failed solves cannot pass. A separate feasible incumbent is retained and receives a fresh hydraulic check before return. Ties prefer fewer outlets, then greater minimum delivered pressure.

The UI displays **best feasible layout found**, not a proven global optimum. A failed bounded search reports no feasible layout found and never labels its best infeasible attempt as a valid plan.

## Core API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Engine status |
| `POST /api/import` | Multipart DXF with `metres_per_unit`, `source_x`, `source_y`, `diameter_mm` |
| `POST /api/optimize` | `{network, settings}` → HTTP 202 job ID |
| `GET /api/jobs/{id}` | Progress and result |
| `POST /api/jobs/{id}/cancel` | Stop at the next progress boundary |

Only one search runs at a time. Job records are in memory, capped at 20, pruned after one hour when a new job is queued, and lost on restart. The old scenario, library, comparison, stress, and evaluation routes are removed.

## Validate

```sh
uv run pytest -q
node --test tests/test_frontend.mjs
node --check web/app.js
```

All **145 automated tests pass: 129 Python tests and 16 frontend logic tests**. The frontend suite uses isolated elements and mocked API responses; it does not access a live browser or verify rendered layout. Tests cover hydraulic equations/units, nonlinear changes with flow, diameter and roughness, continuity, coverage overlap, hard constraints even at low penalty weight, repeatability, graph/schema validation, DXF ingestion, architectural context, unsupported files, and the import-to-optimization flow. Pressure regressions distinguish source pressure from the acceptance threshold at a fixed coverage radius and verify the industrial drawing at minimum pressures of 1.5, 6.5, 6.7, 6.9, and 7.1 bar. Import checks cover Windows line endings, multiple pipe layers, marked candidates on pipe interiors, automatic/manual sources, labeled room risks, and off-pipe or ambiguous data rejection. The sample network helper is used by tests and is not loaded by the website.

The extended suite adds exact pressure/capacity boundaries, an independent closed-form hydraulic reference, exhaustive placement comparisons on a small network, equivalent CAD imports in all four drawing units, an 18-case industrial settings sweep, an 80-candidate extreme-input check, injected solver failures, and concurrent-request/cancellation recovery. Browser checks exercise every setup control and confirm that edits clear stale results. Coverage percentages are bounded to 0–100 despite floating-point summation, and reimporting no longer silently reduces K. The accuracy suite additionally checks analytic circle/lens areas, tiny and concave regions, additive risk layers, finite-but-inconsistent solver outputs, local swaps, and pressure-margin tie breaking. See [TEST_REPORT.md](TEST_REPORT.md) for the test conditions, observed results, and limits.

## Model scope

The model assumes steady-state fixed simultaneous demands, constant source head, explicit pipe/risk layers, and radius coverage. It does not model walls as coverage barriers, hose routes, actual pump curves, transient flow, occupancy-specific code rules, or inferred chemical risk. The minimum pressure is a design input, not statutory certification. Supply/risk data must be supplied and verified for engineering use.

References: [Shapely circle buffering](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html), [WNTR hydraulics](https://usepa.github.io/WNTR/hydraulics.html), [WNTR units](https://usepa.github.io/WNTR/units.html), [DEAP tools](https://deap.readthedocs.io/en/master/api/tools.html).
