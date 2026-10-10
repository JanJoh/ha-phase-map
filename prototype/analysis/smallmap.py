import json,sys,math,statistics as st
S={}
for f in sys.argv[1:]:
    d=json.load(open(f))['data']
    if d.get('warnings'): print("warn:",d['warnings'])
    for e in d['entities']:
        if e['has_more']: print("capped:",e['entity_id'],len(e['statistics']),'/',e['total_count'])
        S.setdefault(e['entity_id'],{}).update({r['start']:r['mean'] for r in e['statistics'] if r.get('mean') is not None})
L=['sensor.current_l1','sensor.current_l2','sensor.current_l3']
ts=sorted(set.intersection(*[set(S[l]) for l in L])); print(f"{len(ts)} points = {len(ts)/288:.1f} days")
for k,v in sorted(S.items()):
    if k in L: continue
    t=[x for x in ts if x in v]
    if not t: print(f"{k[7:]:45} no data"); continue
    W=[v[x] for x in t]; 
    pairs=[(t[i-1],t[i]) for i in range(1,len(t)) if t[i]-t[i-1]==300000]
    dx=[(v[b]-v[a])/230 for a,b in pairs]
    thr=max(15/230, 0.25*max(abs(x) for x in dx) if dx else 0)
    idx=[i for i,x in enumerate(dx) if abs(x)>=thr]
    res=[]
    for l in L:
        dl=[S[l][b]-S[l][a] for a,b in pairs]
        sgn=[dl[i]*(1 if dx[i]>0 else -1) for i in idx]
        mag=st.mean(abs(dx[i]) for i in idx) if idx else float('nan')
        if len(sgn)>3:
            m=st.mean(sgn); se=st.stdev(sgn)/math.sqrt(len(sgn)); res.append((m/mag, m/se if se else 0))
        else: res.append((float('nan'),0))
    best=max(range(3),key=lambda i: res[i][1])
    verdict = f"L{best+1}" if res[best][1]>=3 and res[best][0]>0.4 and all(res[best][1]>2*res[j][1] for j in range(3) if j!=best) else "unclear"
    print(f"{k[7:]:45} avg {st.mean(W):6.1f} W max {max(W):6.0f} W  steps {len(idx):4d} (>= {thr*230:.0f} W)  gain/t L1 {res[0][0]:+.2f}/{res[0][1]:+4.1f}  L2 {res[1][0]:+.2f}/{res[1][1]:+4.1f}  L3 {res[2][0]:+.2f}/{res[2][1]:+4.1f}  -> {verdict}")
