import json,sys,math
S={}
for f in sys.argv[1:]:
    for e in json.load(open(f))['data']['entities']:
        if e['has_more']: print('note:',f.split('-')[-1],e['entity_id'],'got',len(e['statistics']),'of',e['total_count'])
        S.setdefault(e['entity_id'],{}).update({r['start']:r.get('mean') for r in e['statistics'] if r.get('mean') is not None})
L=['sensor.current_l1','sensor.current_l2','sensor.current_l3']
ts=sorted(set.intersection(*[set(S[l]) for l in L]))
print(f"window: {len(ts)} five-minute points = {len(ts)/288:.1f} days")
def corr(a,b):
    n=len(a); ma=sum(a)/n; mb=sum(b)/n
    sa=math.sqrt(sum((x-ma)**2 for x in a)); sb=math.sqrt(sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb) for x,y in zip(a,b))/(sa*sb) if sa and sb else float('nan')
for k,v in sorted(S.items()):
    if k in L: continue
    t=[x for x in ts if x in v]; unit=1.0 if 'current' in k else 1/230
    xs=[v[x]*unit for x in t]; mx=max(xs); act=sum(1 for x in xs if x>0.5)/len(xs)*100
    pairs=[(t[i-1],t[i]) for i in range(1,len(t)) if t[i]-t[i-1]==300000]
    dx=[v[b]*unit-v[a]*unit for a,b in pairs]
    # use only intervals where the sub-meter changed noticeably
    idx=[i for i,x in enumerate(dx) if abs(x)>0.8]
    rs=[]; sl=[]
    for l in L:
        dl=[S[l][b]-S[l][a] for a,b in pairs]
        rs.append(corr(dx,dl))
        num=sum(dx[i]*dl[i] for i in idx); den=sum(dx[i]**2 for i in idx)
        sl.append(num/den if den else float('nan'))
    best=max(range(3),key=lambda i: rs[i] if not math.isnan(rs[i]) else -9)
    note="idle" if mx<=0.5 else f"{len(idx)} steps"
    print(f"{k[7:]:50} max {mx:5.1f}A act {act:5.1f}%  r={'/'.join(f'{r:+.2f}' for r in rs)}  step-gain={'/'.join(f'{s:+.2f}' for s in sl)}  -> L{best+1} ({note})")
