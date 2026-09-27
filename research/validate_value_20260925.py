"""Fixed-rule incremental replication; no production writes or parameter search.
Usage: python research/validate_value_20260925.py <release-tar>
Reuses historical valuation/price definitions, reports missing outcomes explicitly.
Not registered OOS: historical universe/mapping and same-close execution limitations remain.
"""
import json
import pathlib
import sys
import tarfile
import tempfile
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=FutureWarning)
ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'validation_20260925'
OUT.mkdir(exist_ok=True)
cache = pathlib.Path(tempfile.mkdtemp(prefix='us_value_validation_'))
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    for name in ('us_ohlcv.db', 'us_fundamentals.db'):
        member = next(m for m in archive.getmembers() if pathlib.PurePosixPath(m.name).name == name)
        with archive.extractfile(member) as src, (cache / name).open('wb') as dst:
            import shutil
            shutil.copyfileobj(src, dst)
print('Extracted read-only analysis copies', flush=True)
# Load only definitions and data preparation from the existing research script.
source = (ROOT / 'us_sec_scan_followup.py').read_text(encoding='utf-8')
prefix = source.split('anchors = ')[0]
prefix = prefix.replace('"us-screener-data/us_ohlcv.db"', repr((cache / 'us_ohlcv.db').as_posix()))
prefix = prefix.replace('"us-screener-data/us_fundamentals.db"', repr((cache / 'us_fundamentals.db').as_posix()))
ns = {}
exec(compile(prefix, 'existing_research_definitions', 'exec'), ns)
C, RC, V = (ns[k] for k in ('C', 'RC', 'V'))
counts = C.notna().sum()
# Remove isolated holiday rows, as in the 2026-09-16 research script.
ds = [d for d in sorted(C.columns) if counts[d] >= counts.max() * .1]
ns['ds'] = ds
old = pd.read_csv(ROOT / 'us_sec_scan_frame3.csv', dtype={'date': str})
cutoff = old.date.max()
rows = []
for i in range(252, len(ds) - 20, 5):
    if ds[i] <= cutoff:
        continue
    idx, amt = ns['guard_universe'](i)
    ms = ns['mus_score'](i, idx, amt)
    F = ns['val_factors'](i, idx)
    vs = ns['val3_score'](F)
    both = vs.index.intersection(ms.index)
    pool = both[vs.loc[both] >= vs.loc[both].quantile(.6)]
    sc6 = ms + sum(F[f].rank(pct=True).reindex(ms.index).fillna(.5) for f in ('ep','bm','cfoy'))
    selections = {'ew': idx, 'mus50': ms.nlargest(50).index,
                  'val50': vs.nlargest(50).index,
                  'vm_inter': ms.loc[pool].nlargest(50).index,
                  'vm_sum6': sc6.nlargest(50).index}
    fwd = C[ds[i+20]] / C[ds[i]] - 1
    record = {'date': ds[i], 'end': ds[i+20], 'post_definition': ds[i] > '20260830'}
    for name, sel in selections.items():
        r = fwd.reindex(sel).replace([np.inf, -np.inf], np.nan)
        record[name] = float(r.mean()*100)
        record[name+'_n'] = len(sel)
        record[name+'_missing'] = int(r.isna().sum())
    rows.append(record)
    print(record, flush=True)
R = pd.DataFrame(rows)
R.to_csv(OUT / 'incremental_20d.csv', index=False)
summary = {'latest_price': ds[-1], 'last_mature_20d_date': ds[-21],
           'verdict': 'NOT_VALIDATED: insufficient sample and missing outcomes',
           'partial_price_dates': [d for d in ds[-25:] if counts[d] < .9 * counts[ds[-25:]].max()],
           'old_last_anchor': cutoff, 'n_new_anchors': len(R),
           'post_definition_anchors': int(R.post_definition.sum()) if len(R) else 0,
           'recent_date_counts': {d:int(counts[d]) for d in ds[-25:]},
           'comparisons': {}}
if len(R):
    for name in ('mus50','val50','vm_inter','vm_sum6'):
        delta = R[name]-R.mus50
        summary['comparisons'][name] = {
            'mean_return_pct': float(R[name].mean()),
            'mean_excess_ew_pp': float((R[name]-R.ew).mean()),
            'mean_delta_mus_pp': float(delta.mean()),
            'n': len(R), 'ci95': None,
            'ci_reason': 'Too few overlapping weekly anchors for reliable interval',
            'missing_outcomes': int(R[name+'_missing'].sum())}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
