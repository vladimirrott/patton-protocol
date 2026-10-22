import sys
from pathlib import Path
M = [
 ('        else:\n            break\n    return reps', '        else:\n            continue\n    return reps'),
 ('            elif res["a"] < prereg.FLOOR:', '            elif False:'),
 ('            allowed = math.floor(p.threshold * n)', '            allowed = math.floor(p.threshold * n) + 1'),
 ('            scored = r2_scores(rows, "oracle")', '            scored = r2_scores(rows, "approve-all")'),
 ('                if res["point"] >= margin and p_against < stats.ALPHA:', '                if False:'),
 ('verdict="SUPPORTED" if upper < p.threshold else', 'verdict="SUPPORTED" if upper < p.threshold + 5 else'),
 ('        if cdf(mid) > alpha:', '        if cdf(mid) < alpha:'),
 ('    return [r for r in rows if r["rep"] in keep and', '    return [r for r in rows if True and'),
 ('                if entry["id"].startswith(f"{out_dir.name}/"):', '                if True:'),
 ('            elif stats.flag_ceiling_floor((res["a"] + res["b"]) / 2, *prereg.CEILING_FLOOR):', '            elif False:'),
]
old, new = M[int(sys.argv[1])]
p = Path("lab/patton_lab/cc/report.py"); s = p.read_text(); assert s.count(old) == 1, old
p.write_text(s.replace(old, new))
