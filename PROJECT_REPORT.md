# FirePlan: CAD to optimal outlet layout

The project now has one page with CAD import, one optimal setup, and a plan workspace. It applies two syllabus topics: **Genetic Algorithms (Unit VI)** and **constrained optimization using quadratic penalties (Unit IV)**.

The outer GA selects binary outlet placements; the inner WNTR hydraulic solver checks fixed simultaneous demand, flow continuity, pressure loss, and source capacity. The objective maximizes sampled area-weighted risk coverage without duplicate credit for overlapping outlets within a zone. Every returned layout must pass hard hydraulic and count constraints and a fresh final solve.

```text
DXF → validated pipe graph + risk polygons + CAD context
    → GA + normalized quadratic penalties
    → independently checked feasible layout
    → one SVG workspace
```

The setup exposes K, source/minimum pressure, capacity, outlet demand, radius, uniform diameter, CAD units, and source coordinates. Search defaults are fixed at population 28, 16 generations, 250 unique evaluations, λ=500, and seed 42. The app reports the best feasible layout found within these limits and does not certify global optimality or regulatory compliance.

See [README.md](README.md) for the mathematical constraints, parameters, CAD requirements, architecture, API, testing, and model limits. Local archives and generated output are excluded from the published repository.
