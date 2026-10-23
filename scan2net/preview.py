"""An SVG picture of the result: slice points, walkable space, the network,
the exit(s) and locations - to check everything at a glance."""

import math


def write_svg(path, res, width=1400):
    polys = res.polygons
    xy = res.slice_xy
    pts = []
    if xy is not None and len(xy):
        pts += [(float(xy[:, 0].min()), float(xy[:, 1].min())), (float(xy[:, 0].max()), float(xy[:, 1].max()))]
    for p in polys:
        b = p.bounds
        pts += [(b[0], b[1]), (b[2], b[3])]
    if not pts:
        return
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    k = width / max(x1 - x0, 1e-9)
    h = (y1 - y0) * k
    m = 24

    def P(x, y):
        return "%.1f,%.1f" % (m + (x - x0) * k, m + (y1 - y) * k)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
           % (width + 2 * m, h + 2 * m + 30, width + 2 * m, h + 2 * m + 30),
           '<rect width="100%" height="100%" fill="#181a1e"/>']
    for p in polys:
        d = " ".join("M" + " L".join(P(x, y) for x, y in r.coords) + " Z" for r in [p.exterior] + list(p.interiors))
        out.append('<path d="%s" fill="#5ac878" fill-opacity="0.18" stroke="#5ac878" stroke-width="1" '
                   'fill-rule="evenodd"/>' % d)
    if xy is not None and len(xy):
        step = max(1, len(xy) // 40000)
        out.append('<g fill="#8a8f98">' + "".join(
            '<rect x="%.1f" y="%.1f" width="1.4" height="1.4"/>' % (m + (x - x0) * k - .7, m + (y1 - y) * k - .7)
            for x, y in xy[::step]) + "</g>")
    if res.edges:
        pos = {v.number: (v.xyz[0] / 1000.0, v.xyz[1] / 1000.0) for v in res.vertices}
        d = "".join("M%s L%s " % (P(*pos[a]), P(*pos[b])) for a, b, _ in res.edges)
        out.append('<path d="%s" stroke="#f2c94c" stroke-width="0.8" stroke-opacity="0.7" fill="none"/>' % d)
        for v in res.vertices:
            vt = dict(v.attrs).get("VTYPE", "DEFAULT")
            col, r = {"EXIT": ("#f05050", 7), "LOCN": ("#5ac878", 5)}.get(vt, ("#f2994a", 2.2))
            out.append('<circle cx="%s" cy="%s" r="%g" fill="%s"/>' % (*P(*pos[v.number]).split(","), r, col))
    bar = 10 ** math.floor(math.log10(max((x1 - x0) / 5, 1e-9)))
    yb = h + 2 * m + 12
    out.append('<line x1="%d" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#ddd" stroke-width="3"/>' % (m, yb, m + bar * k, yb))
    out.append('<text x="%d" y="%.1f" fill="#ddd" font-family="sans-serif" font-size="13">%g m   '
               'grey: slice points · green: walkable space · yellow: route network · red: exit · '
               'green dots: locations</text>' % (m + bar * k + 10, yb + 5, bar))
    out.append("</svg>")
    with open(path, "w") as f:
        f.write("\n".join(out))
